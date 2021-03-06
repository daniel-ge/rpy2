"""
R objects as Python objects.

The module is structured around the singleton r of class R,
that represents an embedded R.

License: GPLv2+

"""

import array
import contextlib
import os
from functools import partial
import types
import rpy2.rinterface as rinterface
import rpy2.rlike.container as rlc

from rpy2.robjects.robject import RObjectMixin, RObject
import rpy2.robjects.functions
from rpy2.robjects.environments import Environment
from rpy2.robjects.methods import methods_env
from rpy2.robjects.methods import RS4

from . import conversion
from . import vectors
from . import language

from rpy2.rinterface import (Sexp,
                             SexpVector,
                             SexpClosure,
                             SexpEnvironment,
                             SexpS4,
                             StrSexpVector,
                             SexpExtPtr)

from rpy2.robjects.functions import Function
from rpy2.robjects.functions import SignatureTranslatedFunction


_globalenv = rinterface.globalenv
_reval = rinterface.baseenv['eval']

BoolVector = vectors.BoolVector
IntVector = vectors.IntVector
FloatVector = vectors.FloatVector
ComplexVector = vectors.ComplexVector
StrVector = vectors.StrVector
FactorVector = vectors.FactorVector
Vector = vectors.Vector
PairlistVector = vectors.PairlistVector
ListVector = vectors.ListVector
DateVector = vectors.DateVector
POSIXct = vectors.POSIXct
POSIXlt = vectors.POSIXlt
Array = vectors.Array
Matrix = vectors.Matrix
DataFrame = vectors.DataFrame

# Missing values.
NA_Real = rinterface.NA_Real
NA_Integer = rinterface.NA_Integer
NA_Logical = rinterface.NA_Logical
NA_Character = rinterface.NA_Character
NA_Complex = rinterface.NA_Complex
NULL = rinterface.NULL


def reval(string, envir=_globalenv):
    """ Evaluate a string as R code
    - string: a string
    - envir: an environment in which the environment should take place
             (default: R's global environment)
    """
    p = rinterface.parse(string)
    res = _reval(p, envir=envir)
    return res


default_converter = conversion.Converter('base empty converter')


@default_converter.rpy2py.register(RObject)
def _rpy2py_robject(obj):
    return obj


def _vector_matrix_array(obj, vector_cls, matrix_cls, array_cls):
    # Should it be promoted to array or matrix ?
    try:
        dim = obj.do_slot("dim")
        if len(dim) == 2:
            return matrix_cls
        else:
            return array_cls
    except Exception:
        return vector_cls


def sexpvector_to_ro(obj):

    if not isinstance(obj, rinterface.SexpVector):
        raise ValueError('%s is not an R vector.' % obj)

    rcls = obj.rclass

    if 'data.frame' in rcls:
        cls = vectors.DataFrame
    # TODO: There no case/switch statement in Python, but may be
    # there is a more elegant way to implement this.
    elif obj.typeof == rinterface.RTYPES.INTSXP:
        if 'factor' in rcls:
            cls = vectors.FactorVector
        else:
            cls = _vector_matrix_array(obj, vectors.IntVector,
                                       vectors.IntMatrix, vectors.IntArray)
    elif obj.typeof == rinterface.RTYPES.REALSXP:
        if vectors.POSIXct.isrinstance(obj):
            cls = vectors.POSIXct
        else:
            cls = _vector_matrix_array(obj, vectors.FloatVector,
                                       vectors.FloatMatrix, vectors.FloatArray)
    elif obj.typeof == rinterface.RTYPES.LGLSXP:
        cls = _vector_matrix_array(obj, vectors.BoolVector,
                                   vectors.BoolMatrix, vectors.BoolArray)
    elif obj.typeof == rinterface.RTYPES.STRSXP:
        cls = _vector_matrix_array(obj, vectors.StrVector,
                                   vectors.StrMatrix, vectors.StrArray)
    elif obj.typeof == rinterface.RTYPES.VECSXP:
        cls = vectors.ListVector
    elif obj.typeof == rinterface.RTYPES.LISTSXP:
        cls = PairlistVector
    elif obj.typeof == rinterface.RTYPES.LANGSXP:
        if 'formula' in rcls:
            cls = Formula
        else:
            cls = language.LangVector
    elif obj.typeof == rinterface.RTYPES.CPLXSXP:
        cls = _vector_matrix_array(obj, vectors.ComplexVector,
                                   vectors.ComplexMatrix, vectors.ComplexArray)
    elif obj.typeof == rinterface.RTYPES.RAWSXP:
        cls = _vector_matrix_array(obj, vectors.ByteVector,
                                   vectors.ByteMatrix, vectors.ByteArray)
    else:
        cls = None

    if cls is not None:
        return cls(obj)
    else:
        return obj


default_converter.rpy2py.register(SexpVector, sexpvector_to_ro)


TYPEORDER = {bool: (0, BoolVector),
             int: (1, IntVector),
             float: (2, FloatVector),
             complex: (3, ComplexVector),
             str: (4, StrVector)}


def sequence_to_vector(lst):
    curr_typeorder = -1
    i = None
    for i, elt in enumerate(lst):
        cls = type(elt)
        if cls in TYPEORDER:
            if TYPEORDER[cls][0] > curr_typeorder:
                curr_typeorder, curr_type = TYPEORDER[cls]
        else:
            raise ValueError('The element %i in the list has a type '
                             'that cannot be handled.' % i)
    if i is None:
        raise ValueError('The parameter "lst" is an empty sequence. '
                         'The type of the corresponding R vector cannot '
                         'be determined.')
    res = curr_type(lst)
    return res


@default_converter.py2rpy.register(rinterface._MissingArgType)
def _py2rpy_missingargtype(obj):
    return obj


@default_converter.py2rpy.register(bool)
def _py2rpy_bool(obj):
    return obj


@default_converter.py2rpy.register(int)
def _py2rpy_int(obj):
    return obj


@default_converter.py2rpy.register(float)
def _py2rpy_float(obj):
    return obj


@default_converter.py2rpy.register(bytes)
def _py2rpy_bytes(obj):
    return obj


@default_converter.py2rpy.register(str)
def _py2rpy_str(obj):
    return obj


@default_converter.rpy2py.register(SexpClosure)
def _rpy2py_sexpclosure(obj):
    return SignatureTranslatedFunction(obj)


@default_converter.rpy2py.register(SexpEnvironment)
def _rpy2py_sexpenvironment(obj):
    return Environment(obj)


class NameClassMap(object):
    """Map a name (R class name) to a Python class."""

    def __init__(self, defaultcls):
        self._default = defaultcls
        self._map = dict()

    def __contains__(self, key: str):
        return key in self._map

    def __delitem__(self, key: str):
        del self._map[key]

    def __getitem__(self, key: str):
        return self._map[key]

    def __setitem__(self, key:str, value):
        assert issubclass(value, self._default)
        self._map[key] = value

    def find_key(self, keys):
        """Find the first mapping key in a sequence of names (keys).

        Returns None if no mapping key."""
        for k in keys:
            if k in self._map:
                return k
        return None

    def find(self, keys):
        """Find the first mapping in a sequence of names (keys).

        Returns the default class (specified when creating the
        instance if no mapping key."""
        k = self.find_key(keys)
        if k:
            cls = self._map[k]
        else:
            cls = self._default
        return cls


class NameClassMapContext(object):

    def __init__(self, nameclassmap: NameClassMap,
                 d: dict):
        self._nameclassmap = nameclassmap
        self._d = d
        self._keep = []

    def __enter__(self):
        nameclassmap = self._nameclassmap
        for k, v in self._d.items():
            if k in nameclassmap:
                restore = True
                orig_v = nameclassmap[k]
            else:
                restore = False
                orig_v = None
            self._keep.append((k, restore, orig_v))
            nameclassmap[k] = v

    def __exit__(self, exc_type, exc_val, exc_tb):
        nameclassmap = self._nameclassmap
        for k, restore, orig_v in self._keep:
            if restore:
                nameclassmap[k] = orig_v
            else:
                del(nameclassmap[k])
        return False


_rs4_map = NameClassMap(RS4)

rs4map_context = partial(NameClassMapContext, _rs4_map)


@default_converter.rpy2py.register(SexpS4)
def _rpy2py_sexps4(obj):
    cls = _rs4_map.find(methods_env['extends'](obj.rclass))
    return cls(obj)


@default_converter.rpy2py.register(SexpExtPtr)
def _rpy2py_sexpextptr(obj):
    return obj


@default_converter.rpy2py.register(object)
def _rpy2py_object(obj):
    return RObject(obj)


@default_converter.rpy2py.register(type(NULL))
def _rpy2py_null(obj):
    return obj


# TODO: delete ?
def default_py2ri(o):
    """ Convert an arbitrary Python object to a
    :class:`rpy2.rinterface.Sexp` object.
    Creates an R object with the content of the Python object,
    wich means data copying.
    :param o: object
    :rtype: :class:`rpy2.rinterface.Sexp` (and subclasses)
    """
    pass


@default_converter.py2rpy.register(RObject)
def _py2rpy_robject(obj):
    return rinterface.Sexp(obj)


@default_converter.py2rpy.register(Sexp)
def _py2rpy_sexp(obj):
    return obj


@default_converter.py2rpy.register(array.array)
def _py2rpy_array(obj):
    if obj.typecode in ('h', 'H', 'i', 'I'):
        res = IntVector(obj)
    elif obj.typecode in ('f', 'd'):
        res = FloatVector(obj)
    else:
        raise(
            ValueError('Nothing can be done for this array '
                       'type at the moment.')
        )
    return res


default_converter.py2rpy.register(int,
                                  lambda x: x)


@default_converter.py2rpy.register(list)
def _py2rpy_list(obj):
    return vectors.ListVector(
        rinterface.ListSexpVector(
            [conversion.py2rpy(x) for x in obj]
        )
    )


@default_converter.py2rpy.register(rlc.TaggedList)
def _py2rpy_taggedlist(obj):
    res = vectors.ListVector(
        rinterface.ListSexpVector([conversion.py2rpy(x) for x in obj])
    )
    res.do_slot_assign('names', rinterface.StrSexpVector(obj.tags))
    return res


@default_converter.py2rpy.register(complex)
def _py2rpy_complex(obj):
    return obj


@default_converter.py2rpy.register(types.FunctionType)
def _function_to_rpy(func):
    def wrap(*args):
        res = func(*args)
        res = conversion.py2ro(res)
        return res
    rfunc = rinterface.rternalize(wrap)
    return conversion.rpy2py(rfunc)


@default_converter.rpy2py.register(object)
def _(obj):
    return obj


class Formula(RObjectMixin, rinterface.Sexp):

    def __init__(self, formula, environment=_globalenv):
        if isinstance(formula, str):
            inpackage = rinterface.baseenv["::"]
            asformula = inpackage(rinterface.StrSexpVector(['stats', ]),
                                  rinterface.StrSexpVector(['as.formula', ]))
            formula = rinterface.StrSexpVector([formula, ])
            robj = asformula(formula,
                             env=environment)
        else:
            robj = formula
        super(Formula, self).__init__(robj)

    def getenvironment(self):
        """ Get the environment in which the formula is finding its symbols."""
        res = self.do_slot(".Environment")
        res = conversion.rpy2py(res)
        return res

    def setenvironment(self, val):
        """ Set the environment in which a formula will find its symbols."""
        if not isinstance(val, rinterface.SexpEnvironment):
            raise TypeError("The environment must be an instance of" +
                             " rpy2.rinterface.Sexp.environment")
        self.do_slot_assign(".Environment", val)

    environment = property(getenvironment, setenvironment,
                           "R environment in which the formula will look for" +
                           " its variables.")


class R(object):
    """
    Singleton representing the embedded R running.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            rinterface.initr_simple()
            cls._instance = object.__new__(cls)
        return cls._instance

    def __getattribute__(self, attr):
        try:
            return super(R, self).__getattribute__(attr)
        except AttributeError as ae:
            orig_ae = str(ae)

        try:
            return self.__getitem__(attr)
        except LookupError:
            raise AttributeError(orig_ae)

    def __getitem__(self, item):
        res = _globalenv.find(item)
        res = conversion.rpy2py(res)
        if hasattr(res, '__rname__'):
            res.__rname__ = item
        return res

    # TODO: check that this is properly working
    def __cleanup__(self):
        rinterface.endEmbeddedR()
        del(self)

    def __str__(self):
        version = self['version']
        s = [super(R, self).__str__()]
        s.extend('%s: %s' % (n, val[0])
                 for n, val in zip(version.names, version))
        return os.linesep.join(s)

    def __call__(self, string):
        p = rinterface.parse(string)
        res = self.eval(p)
        return conversion.rpy2py(res)


r = R()

conversion.set_conversion(default_converter)

globalenv = conversion.converter.rpy2py(_globalenv)
baseenv = conversion.converter.rpy2py(rinterface.baseenv)
emptyenv = conversion.converter.rpy2py(rinterface.emptyenv)
