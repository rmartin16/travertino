from collections import defaultdict
from warnings import filterwarnings, warn

from .colors import color
from .constants import BOTTOM, LEFT, RIGHT, TOP

# Make sure deprecation warnings are shown by default
filterwarnings("default", category=DeprecationWarning)


class Choices:
    "A class to define allowable data types for a property"

    def __init__(
        self,
        *constants,
        default=None,  # DEPRECATED
        string=False,
        integer=False,
        number=False,
        color=False,
    ):
        if default is not None:
            warn(
                "The `default` argument to Choices.__init__ is deprecated. "
                "Providing no initial value to a property using it is sufficient.",
                DeprecationWarning,
                stacklevel=2,
            )

        self.constants = set(constants)

        self.string = string
        self.integer = integer
        self.number = number
        self.color = color

        self._options = sorted(str(c).lower().replace("_", "-") for c in self.constants)
        if self.string:
            self._options.append("<string>")
        if self.integer:
            self._options.append("<integer>")
        if self.number:
            self._options.append("<number>")
        if self.color:
            self._options.append("<color>")

    def validate(self, value):
        if self.string:
            try:
                return value.strip()
            except AttributeError:
                pass
        if self.integer:
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
        if self.number:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        if self.color:
            try:
                return color(value)
            except ValueError:
                pass
        for const in self.constants:
            if value == const:
                return const

        raise ValueError(f"{value!r} is not a valid value")

    def __str__(self):
        return ", ".join(self._options)


class validated_property:
    def __init__(self, choices, initial=None):
        """Define a simple validated property attribute.

        :param choices: The available choices.
        :param initial: The initial value for the property.
        """
        self.choices = choices
        self.initial = None

        try:
            # If an initial value has been provided, it must be consistent with
            # the choices specified.
            if initial is not None:
                self.initial = choices.validate(initial)
        except ValueError:
            # Unfortunately, __set_name__ hasn't been called yet, so we don't know the
            # property's name.
            raise ValueError(
                f"Invalid initial value {initial!r}. Available choices: {choices}"
            )

    def __set_name__(self, owner, name):
        self.name = name
        owner._PROPERTIES[owner].add(name)
        owner._ALL_PROPERTIES[owner].add(name)

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self

        value = getattr(obj, f"_{self.name}", None)
        return self.initial if value is None else value

    def __set__(self, obj, value):
        if value is self:
            # This happens during autogenerated dataclass __init__ when no value is
            # supplied.
            return

        if value is None:
            raise ValueError(
                "Python `None` cannot be used as a style value; "
                f"to reset a property, use del `style.{self.name}`"
            )

        try:
            value = self.choices.validate(value)
        except ValueError:
            raise ValueError(
                f"Invalid value {value!r} for property {self.name}; "
                f"Valid values are: {self.choices}"
            )

        if value != getattr(obj, f"_{self.name}", None):
            setattr(obj, f"_{self.name}", value)
            obj.apply(self.name, value)

    def __delete__(self, obj):
        try:
            delattr(obj, f"_{self.name}")
        except AttributeError:
            pass
        else:
            obj.apply(self.name, self.initial)


class directional_property:
    DIRECTIONS = [TOP, RIGHT, BOTTOM, LEFT]
    ASSIGNMENT_SCHEMES = {
        #   T  R  B  L
        1: [0, 0, 0, 0],
        2: [0, 1, 0, 1],
        3: [0, 1, 2, 1],
        4: [0, 1, 2, 3],
    }

    def __init__(
        self, name_format, choices=None, initial=None, _create_directions=True
    ):
        """Define a property attribute that proxies for top/right/bottom/left alternatives.

        :param name_format: The format from which to generate subproperties. "{}" will
            be replaced with "_top", etc.
        :param choices: The available choices.
        :param initial: What value to set initially. None means no initial value.
        """
        if _create_directions and choices is None:
            raise TypeError(
                f"{self.__class__.__name__}.__init__() missing 1 required positional "
                "argument: 'choices'"
            )
        self.name_format = name_format
        self.choices = choices
        self.initial = initial
        self._create_directions = _create_directions

    def __set_name__(self, owner, name):
        self.name = name
        owner._ALL_PROPERTIES[owner].add(self.name)

        # Dynamically create the actual properties. They still need to be defined as
        # class attributes in order to be in the dataclass init, but they don't have to
        # be set to anything.
        if self._create_directions:
            for direction in self.DIRECTIONS:
                prop_name = self.format(direction)
                prop = validated_property(self.choices, self.initial)
                setattr(owner, prop_name, prop)
                # Not called automatically, since this isn't in the class definition:
                prop.__set_name__(owner, prop_name)

    def format(self, direction):
        return self.name_format.format(f"_{direction}")

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self

        return tuple(obj[self.format(direction)] for direction in self.DIRECTIONS)

    def __set__(self, obj, value):
        if value is self:
            # This happens during autogenerated dataclass __init__ when no value is
            # supplied.
            return

        if not isinstance(value, tuple):
            value = (value,)

        if order := self.ASSIGNMENT_SCHEMES.get(len(value)):
            for direction, index in zip(self.DIRECTIONS, order):
                obj[self.format(direction)] = value[index]
        else:
            raise ValueError(
                f"Invalid value for '{self.name}'; value must be a number, or a 1-4 tuple."
            )

    def __delete__(self, obj):
        for direction in self.DIRECTIONS:
            del obj[self.format(direction)]


class BaseStyle:
    """A base class for style declarations.

    Exposes a dict-like interface. Designed for subclasses to be decorated
    with @dataclass(kw_only=True), which most IDEs should be able to interpret and
    provide autocompletion of argument names. On Python < 3.10, init=False can be used
    to still get the keyword-only behavior from the included __init__.
    """

    _PROPERTIES = defaultdict(set)
    _ALL_PROPERTIES = defaultdict(set)

    # Fallback in case subclass isn't decorated as subclass (probably from using
    # previous API) or for pre-3.10, before kw_only argument existed.
    def __init__(self, **style):
        self.update(**style)

    @property
    def _applicator(self):
        return getattr(self, "_assigned_applicator", None)

    @_applicator.setter
    def _applicator(self, value):
        self._assigned_applicator = value

    ######################################################################
    # Interface that style declarations must define
    ######################################################################

    def apply(self, property, value):
        raise NotImplementedError(
            "Style must define an apply method"
        )  # pragma: no cover

    ######################################################################
    # Provide a dict-like interface
    ######################################################################

    def reapply(self):
        for style in self._PROPERTIES[self.__class__]:
            self.apply(style, getattr(self, style))

    def update(self, **styles):
        "Set multiple styles on the style definition."
        for name, value in styles.items():
            name = name.replace("-", "_")
            if name not in self._ALL_PROPERTIES[self.__class__]:
                raise NameError(f"Unknown style {name}")

            setattr(self, name, value)

    def copy(self, applicator=None):
        "Create a duplicate of this style declaration."
        dup = self.__class__()
        dup._applicator = applicator
        for style in self._PROPERTIES[self.__class__]:
            try:
                setattr(dup, style, getattr(self, f"_{style}"))
            except AttributeError:
                pass
        return dup

    def __getitem__(self, name):
        name = name.replace("-", "_")
        if name in self._PROPERTIES[self.__class__]:
            return getattr(self, name)
        raise KeyError(name)

    def __setitem__(self, name, value):
        name = name.replace("-", "_")
        if name in self._PROPERTIES[self.__class__]:
            setattr(self, name, value)
        else:
            raise KeyError(name)

    def __delitem__(self, name):
        name = name.replace("-", "_")
        if name in self._PROPERTIES[self.__class__]:
            delattr(self, name)
        else:
            raise KeyError(name)

    def items(self):
        return [
            (name, value)
            for name in self._PROPERTIES[self.__class__]
            if (value := getattr(self, f"_{name}", None)) is not None
        ]

    def keys(self):
        return {
            name
            for name in self._PROPERTIES[self.__class__]
            if hasattr(self, f"_{name}")
        }

    ######################################################################
    # Get the rendered form of the style declaration
    ######################################################################
    def __str__(self):
        non_default = []
        for name in self._PROPERTIES[self.__class__]:
            try:
                non_default.append((name.replace("_", "-"), getattr(self, f"_{name}")))
            except AttributeError:
                pass

        return "; ".join(f"{name}: {value}" for name, value in sorted(non_default))

    ######################################################################
    # Backwards compatibility
    ######################################################################

    @classmethod
    def validated_property(cls, name, choices, initial=None):
        warn(
            "Defining style properties with class methods is deprecated; use class "
            "attributes instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        prop = validated_property(choices, initial)
        setattr(cls, name, prop)
        prop.__set_name__(cls, name)

    @classmethod
    def directional_property(cls, name):
        warn(
            "Defining style properties with class methods is deprecated; use class "
            "attributes instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        name_format = name % "{}"
        name = name_format.format("")
        prop = directional_property(name_format, _create_directions=False)
        setattr(cls, name, prop)
        prop.__set_name__(cls, name)

    # Kept here for reference, for eventual implementation?

    # def list_property(name, choices, initial=None):
    #     "Define a property attribute that accepts a list of independently validated values."
    #     initial = choices.validate(initial)

    #     def getter(self):
    #         return getattr(self, '_%s' % name, initial)

    #     def setter(self, values):
    #         try:
    #             value = [choices.validate(v) for v in values.split(',')]
    #         except ValueError:
    #             raise ValueError("Invalid value in for list property '%s'; Valid values are: %s" % (
    #                 name, choices
    #             ))

    #         if value != getattr(self, '_%s' % name, initial):
    #             setattr(self, '_%s' % name, value)
    #             self.apply(name, value)

    #     def deleter(self):
    #         try:
    #             delattr(self, '_%s' % name)
    #             self.apply(name, value)
    #         except AttributeError:
    #             # Attribute doesn't exist
    #             pass

    #     _PROPERTIES.add(name)
    #     return property(getter, setter, deleter)
