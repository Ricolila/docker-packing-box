# -*- coding: UTF-8 -*-
import builtins
from tinyscript import inspect, itertools, logging, random, re, string
from tinyscript.helpers import get_terminal_size, is_file, is_folder, is_generator, is_iterable, reduce, \
                               set_exception, txt_terminal_render, zeropad, Path, TempPath
from tinyscript.helpers.expressions import WL_NODES

from .files import Locator
from .formats import expand_formats, format_shortname
from .utils import pd

lazy_load_module("yaml")
set_exception("NotInstantiable", "TypeError")


__all__ = ["dict2", "load_yaml_config", "Item", "MetaBase", "MetaItem"]

_EMPTY_DICT = {}
_EVAL_NAMESPACE = {k: getattr(builtins, k) for k in ["abs", "all", "any", "divmod", "float", "hash", "hex", "id", "int",
                                                     "list", "next", "oct", "ord", "pow", "range", "range2", "round",
                                                     "set", "str", "sum", "tuple", "type"]}
_WL_EXTRA_NODES = ("arg", "arguments", "keyword", "lambda")


_concatn  = lambda l, n: reduce(lambda a, b: a[:n]+b[:n], l, stop=lambda x: len(x) > n)
_len      = lambda i: sum(1 for _ in i) if is_generator(i) else len(i)
_max      = lambda l, *a, **kw: None if len(l2 := [x for x in l if x is not None]) == 0 else max(l2, *a, **kw)
_min      = lambda l, *a, **kw: None if len(l2 := [x for x in l if x is not None]) == 0 else min(l2, *a, **kw)
_repeatn  = lambda s, n: (s * (n // len(s) + 1))[:n]
_sec_name = lambda s: getattr(s, "real_name", getattr(s, "name", s))
_size     = lambda exe, ratio=.1, blocksize=512: round(int(exe['size'] * ratio) / blocksize + .5) * blocksize
_val      = lambda o: getattr(o, "value", o)


def _randbytes(n, unique=True):
    if not unique:
        return random.randbytes(n)
    if n > 256:
        raise ValueError("Cannot produce more than 256 distinct bytes")
    s, alphabet = b"", bytearray([i for i in range(256)])
    for i in range(n):
        c = random.choice(alphabet)
        s += bytes([c])
        alphabet.remove(c)
    return s


def _select(apply_func=None):
    def _wrapper(lst=(), random_lst=(), inclusions=(), exclusions=()):
        """ Helper for selecting the first argument of a list given inclusions or exclusions, then choosing randomly
             among a given list when the first list is consumed. """
        _list = lambda l: list(l()) if callable(l) else list(l) if is_iterable(l) else [l]
        _map = lambda l: list(map(apply_func, l)) if isinstance(apply_func, type(lambda: 0)) else l
        # ensure that inputs are lists, allowing to make them from a function if desired
        exc, inc, lst, rlst = _list(exclusions), _list(inclusions), _list(lst), _list(random_lst)
        # map the function to be applied to all input lists
        lst, rlst = _map(lst), _map(rlst)
        exc, inc = _map(exc), _map(inc) or lst + rlst
        # now iterate over the list of choices given first exclusions then inclusions
        for x in lst:
            if x in exc or x not in inc:
                continue
            return x
        # if no entry returned yet, return an element from the random list given exclusions
        return random.choice(rlst, exc, False)
    return _wrapper


class dict2(dict):
    """ Simple extension of dict for defining callable items. """
    def __init__(self, idict=_EMPTY_DICT, **kwargs):
        self.setdefault("name", UNDEF_RESULT)
        self.setdefault("description", "")
        self.setdefault("result", self['name'])
        for f, v in getattr(self.__class__, "_fields", {}).items():
            self.setdefault(f, v)
        logger = idict.pop('logger', kwargs.pop('logger', null_logger))
        super().__init__(idict, **kwargs)
        self.__dict__ = self
        dict2._logger = logger
        if self.result == UNDEF_RESULT:
            raise ValueError(f"{self.name}: 'result' shall be defined")
    
    def __call__(self, data, silent=False, **kwargs):
        d = {k: getattr(random, k) for k in ["choice", "randint", "randrange", "randstr"]}
        d.update({'apply': _apply, 'concatn': _concatn, 'len': _len, 'max': _max, 'min': _min,
                  'printable': string.printable, 'randbytes': _randbytes, 'repeatn': _repeatn, 'select': _select(),
                  'select_section_name': _select(_sec_name), 'size': _size, 'value': _val, 'zeropad': zeropad})
        d.update(_EVAL_NAMESPACE)
        d.update(data)
        kwargs.update(getattr(self, "parameters", {}))
        # execute an expression from self.result (can be a single expression or a list of expressions to be chained)
        def _exec(expr):
            try:
                d.pop('__builtins__', None)  # security issue ; ensure builtins are removed !
                r = eval2(expr, d, {}, whitelist_nodes=WL_NODES + _WL_EXTRA_NODES)
                if len(kwargs) == 0:  # means no parameter provided
                    return r
            except ForbiddenNodeError as e:  # this error type shall always be reported
                dict2._logger.warning(f"Bad expression: {expr}")
                dict2._logger.error(f"{e}")
                raise
            except NameError as e:
                if not silent:
                    name = str(e).split("'")[1]
                    dict2._logger.debug(f"'{name}' is either not computed yet or mistaken")
                raise
            except Exception as e:
                if not silent:
                    dict2._logger.warning(f"Bad expression: {expr}")
                    dict2._logger.exception(e)
                    w = get_terminal_size()[0]
                    dict2._logger.debug("Variables:\n- %s" % \
                        "\n- ".join(string.shorten(f"{k}({type(v).__name__})={v}", w - 2) for k, v in d.items()))
                raise
            try:
                return r(**kwargs)
            except Exception as e:
                if not silent:
                    dict2._logger.warning(f"Bad function: {result}")
                    dict2._logger.error(str(e))
        # now execute expression(s) ; support for multiple expressions must be explicitely enabled for the class
        if not getattr(self.__class__, "_multi_expr", False) and isinstance(self.result, (list, tuple)):
            raise ValueError(f"List of expressions is not supported for the result of {self.__class__.__name__}")
        retv = [_exec(result) for result in (self.result if isinstance(self.result, (list, tuple)) else [self.result])]
        return retv[0] if len(retv) == 1 else tuple(retv)


class MetaBase(type):
    """ This metaclass allows to iterate names over the class-bound registry of the underlying abstraction.
         It also adds a class-level 'source' attribute that can be used to reset the registry. """
    def __iter__(self):
        self()  # trigger registry's lazy initialization
        temp = []
        for base in self.registry.values():
            for name in base.keys():
                if name not in temp:
                    yield name
                    temp.append(name)
    
    def _select(self, format="All", query=None, fields=None, index="name", raw=False, dataframe=False, **kw):
        """ Select a subset based on a Pandas query and return it as a dictionary. """
        _l = self.logger
        # from the registry, based on the required formats, build a DataFrame combining all items
        for i, c in enumerate(expand_formats(format)):
            d = {}
            for record in self.registry.get(c, {}).values():
                for k, v in dict(record).items():  # dict(...) converts the dict2 instance to its full version
                    if k not in d:
                        d[k] = []
                        for _ in range(len(d[index])-1):
                            d[k].append(None)
                    d[k].append(str(v))
            n = len(d[index])
            for k, l in d.items():
                if (n2 := len(l)) < n:
                    for _ in range(n-n2):
                        l.append(None)
            df = pd.DataFrame.from_dict(d) if i == 0 else df.combine_first(pd.DataFrame.from_dict(d))
        if fields:
            if index not in fields:
                fields = [index] + fields
            df = df[fields]
        # now filter the resulting DataFrame with the given query
        if query is not None and query.lower() != "all":
            try:
                df = df.query(query)
            except SyntaxError as e:
                _l.error(f"Bad Pandas query expression: {e}")
                _l.warning("No entry filtered")
            except Exception as e:
                if Path(inspect.getfile(e.__class__)).dirname.parts[-2:] == ('pandas', 'errors') or \
                   isinstance(e, TypeError):
                    _l.error(f"Bad Pandas query expression: {e}")
                    _l.warning("No entry filtered")
                else:
                    raise
        if len(df) == 0:
            _l.warning("No data")
            return None if dataframe or raw else (None, None)
        _l.debug(f"Dataframe queried:\n{df}")
        if dataframe:
            return df
        # only keep the interesting parts of the configuration file based the filtered DataFrame's list of names
        d = {k: v for k, v in load_yaml_config(self._source) if k in df[[index]].values}
        # collect values in the selected items to rebuild the defaults
        attrs = {}
        for k, v in d.items():
            for attr, val in v.items():
                attrs.setdefault(attr, [])
                if val not in attrs[attr]:
                    attrs[attr].append(val)
        # compute the defaults based on collected values
        defaults = {}
        for attr, vals in attrs.items():
            if len(vals) == 1:
                val = vals[0]
                if sum(1 for v in d.values() if val in v.values()) == len(d):
                    defaults[attr] = val
                    for v in d.values():
                        v.pop(attr, None)
        if not raw:
            return defaults, d
        from yaml import dump, SafeDumper
        # custom dumper for inserting blank lines between top-level objects
        class CustomDumper(SafeDumper):
            # inspired by https://stackoverflow.com/a/44284819/3786245
            def write_line_break(self, data=None):
                super().write_line_break(data)
                if len(self.indents) == 1:
                    super().write_line_break()
        return (dump({'defaults': defaults}, width=float("inf")).rstrip() + "\n\n" if len(defaults) > 0 else "") + \
               dump(d, Dumper=CustomDumper, width=float("inf")).rstrip()
    
    def export(self, output="output.csv", format="All", query=None, fields=None, index="name", **kw):
        """ Export items from the registry based on the given executable format, filtered with the given query, with
             only the selected fields, to the given output format. """
        if output in EXPORT_FORMATS.keys():
            output = f"{self.__name__.lower()}-export.{output}"
        kw, df = {}, self._select(format, query, fields, dataframe=True)
        if (ext := Path(output).extension[1:]) != "pickle":
            kw['index'] = False
        if ext in ["json", "yml"]:
            kw.update(orient="records", indent=2)
        if ext == "csv":
            kw.update(sep=";", index=False)
        getattr(df.reset_index(), "to_" + EXPORT_FORMATS.get(ext, ext))(output, **kw)

    def select(self, output=None, format="All", query=None, **kw):
        """ Select a subset based on a Pandas query and either display or dump it. """
        if output is not None:
            dst = Locator(f"conf://{output}", new=True)
            if dst.is_samepath(self._source):
                self.logger.warning("Destination is identical to source ; aborting...")
                return
        if (c := self._select(format, query, raw=True, **kw)) is not None:
            if output is None:
                print(txt_terminal_render(c, pad=(1, 2), syntax="yaml"))
            else:
                with dst.open('wt') as f:
                    f.write(c)
    
    def test(self, files=None, keep=False, **kw):
        """ Tests on some executable files. """
        from tinyscript.helpers import execute_and_log as run
        from .formats import expand_formats
        from ..core.executable import Executable
        d = TempPath(prefix=f"{self.__name__.lower()}-tests-", length=8)
        self.__disp = []
        self()  # force registry computation
        for fmt in expand_formats("All"):
            if fmt not in self.registry.keys():
                self.logger.warning(f"no {self.__name__.lower().rstrip('s')} defined yet for '{fmt}'")
                continue
            l = [f for f in files if Executable(f).format in self._formats_exp] if files else TEST_FILES.get(fmt, [])
            if len(l) == 0:
                continue
            self.logger.info(fmt)
            for exe in l:
                exe = Executable(exe, expand=True)
                tmp = d.joinpath(exe.filename)
                self.logger.debug(exe.filetype)
                run(f"cp {exe} {tmp}")
                n = tmp.filename
                try:
                    self(Executable(tmp))
                    self.logger.success(n)
                except Exception as e:
                    if isinstance(e, KeyError) and exe.format is None:
                        self.logger.error(f"unknown format ({exe.filetype})")
                        continue
                    self.logger.failure(n)
                    self.logger.exception(e)
        if not keep:
            self.logger.debug(f"rm -f {d}")
            d.remove()
    
    @property
    def logger(self):
        if not hasattr(self, "_logger"):
            n = self.__name__.lower()
            self._logger = logging.getLogger(n)
            logging.setLogger(n)
        return self._logger
    
    @property
    def source(self):
        if not hasattr(self, "_source"):
            self.source = None  # use the default source from 'config'
        return self._source
    
    @source.setter
    def source(self, path):
        p = Path(str(path or config[self.__name__.lower()]), expand=True)
        if hasattr(self, "_source") and self._source == p:
            return
        self._source = p
        self.registry = None  # reset the registry


def _apply(f):
    """ Simple decorator for applying an operation to the result of the decorated function. """
    def _wrapper(op):
        def _subwrapper(*a, **kw):
            return op(f(*a, **kw))
        return _subwrapper
    return _wrapper


def _init_metaitem():
    class MetaItem(type):
        __fields__ = {}
        
        def __getattribute__(self, name):
            # this masks some attributes for child classes (e.g. Packer.registry can be accessed, but when the registry
            #  of child classes is computed, the child classes, e.g. UPX, won't be able to get UPX.registry)
            if name in ["get", "iteritems", "mro", "registry"] and self._instantiable:
                raise AttributeError(f"'{self.__name__}' object has no attribute '{name}'")
            return super(MetaItem, self).__getattribute__(name)
        
        @property
        def logger(self):
            if not hasattr(self, "_logger"):
                n = self.__name__.lower()
                self._logger = logging.getLogger(n)
                logging.setLogger(n)
            return self._logger
        
        @property
        def names(self):
            return sorted(x.name for x in self.registry)
        
        @property
        def source(self):
            if not hasattr(self, "_source"):
                self.source = None
            return self._source
        
        @source.setter
        def source(self, path):
            cfg_key, l = f"{self.__name__.lower()}s", self.logger
            # case 1: self is a parent class among Analyzer, Detector, ... ;
            #          then 'source' means the source path for loading child classes
            try:
                p = Path(str(path or config[cfg_key]), expand=True)
                if hasattr(self, "_source") and self._source == p:
                    return
                self._source = p
            # case 2: self is a child class of Analyzer, Detector, ... ;
            #          then 'source' is an attribute that comes from the YAML definition
            except KeyError:
                return
            # now make the registry from the given source path
            def _setattr(i, d):
                for k, v in d.items():
                    if k in ["source", "status"]:
                        
                        setattr(i, k := f"_{k}", v)
                    elif hasattr(i, "parent") and k in ["install", "references", "steps"]:
                        nv = []
                        for l in v:
                            if l == "<from-parent>":
                                for l2 in getattr(glob[i.parent], k, []):
                                    nv.append(l2)
                            else:
                                nv.append(l)
                        setattr(i, k, nv)
                    else:
                        setattr(i, k, v)
                    if k not in self.__fields__.keys():
                        self.__fields__[k] = None
            # open the .conf file associated to the main class (i.e. Detector, Packer, ...)
            glob = inspect.getparentframe().f_back.f_globals
            # remove the child classes of the former registry from the global scope
            for cls in getattr(self, "registry", []):
                glob.pop(cls.cname, None)
            # reset the registry
            self.registry = []
            if not p.exists():
                l.warning(f"'{p}' does not exist ; set back to default")
                p, func = config.DEFAULTS['definitions'][cfg_key]
                p = func(p)
            # start parsing items of the target class
            _cache, cnt = {}, {'tot': 0, 'var': 0}
            l.debug(f"loading {cfg_key} from {p}...")
            for item, data in load_yaml_config(p, ("base", "install", "steps", "variants")):
                # ensure the related item is available in module's globals()
                #  NB: the item may already be in globals in some cases like pbox.items.packer.Ezuri
                if item not in glob:
                    d = dict(self.__dict__)
                    del d['registry']
                    glob[item] = type(item, (self, ), d)
                i = glob[item]
                i._instantiable = True
                # before setting attributes from the YAML parameters, check for 'base' ; this allows to copy all
                #  attributes from an entry originating from another item class (i.e. copying from Packer's equivalent
                #  to Unpacker ; e.g. UPX)
                base = data.get('base')  # detector|packer|unpacker ; DO NOT pop as 'base' is also used for algorithms
                if isinstance(base, str):
                    m = re.match(r"(?i)(detector|packer|unpacker)(?:\[(.*?)\])?$", str(base))
                    if m:
                        data.pop('base')
                        base, bcls = m.groups()
                        base, bcls = base.capitalize(), bcls or item
                        if base == self.__name__ and bcls in [None, item]:
                            raise ValueError(f"{item} cannot point to itself")
                        if base not in _cache.keys():
                            _cache[base] = dict(load_yaml_config(base.lower() + "s"))
                        for k, v in _cache[base].get(bcls, {}).items():
                            # do not process these keys as they shall be different from an item class to another anyway
                            if k in ["steps", "status"]:
                                continue
                            setattr(i, "_" + k if k == "source" else k, v)
                    else:
                        raise ValueError(f"'base' set to '{base}' of {item} discarded (bad format)")
                # check for variants ; the goal is to copy the current item class and to adapt the fields from the
                #  variants to the new classes (note that on the contrary of base, a variant inherits the 'status'
                #  parameter)
                variants, vilist = data.pop('variants', {}), []
                # collect template parameters
                template = variants.pop('_template', {})
                if len(template) > 0:
                    attrs = [k[1:] for k in list(template.keys()) if k.startswith("_")]
                    iterables = [template.pop(f"_{k}") for k in attrs]
                    for values in itertools.product(*iterables):
                        vitem = "-".join(map(str, (item, ) + values))
                        # this create the new variant item if not present
                        variants.setdefault(vitem, {k: v for k, v in template.items()})
                        # this updates existing variant item with template's content
                        for k, v in itertools.chain(zip(attrs, values), template.items()):
                            variants[vitem].setdefault(k, v)
                # create variant classes in globals
                for vitem in variants.keys():
                    d = dict(self.__dict__)
                    del d['registry']
                    vi = glob[vitem] = type(vitem, (self, ), d)
                    vi._instantiable = True
                    vi.parent = item
                    vilist.append(vi)
                # now set attributes from YAML parameters
                for it in [i] + vilist:
                    _setattr(it, data)
                self.registry.append(i())
                cnt['tot'] += 1
                # overwrite parameters specific to variants
                for vitem, vdata in variants.items():
                    vi = glob[vitem]
                    _setattr(vi, vdata)
                    self.registry.append(vi())
                    cnt['var'] += 1
            varcnt = ["", f" ({cnt['var']} variants)"][cnt['var'] > 0]
            l.debug(f"{cnt['tot']} {cfg_key} loaded{varcnt}")
    return MetaItem
lazy_load_object("MetaItem", _init_metaitem)


def _init_item():
    global MetaItem
    MetaItem.__name__  # force the initialization of MetaItem
    class Item(metaclass=MetaItem):
        """ Item abstraction. """
        _instantiable = False
        
        def __init__(self, **kwargs):
            cls = self.__class__
            self.cname = cls.__name__
            self.name = format_shortname(cls.__name__, "-")
            self.type = cls.__base__.__name__.lower()
        
        def __new__(cls, *args, **kwargs):
            """ Prevents Item from being instantiated. """
            if cls._instantiable:
                return object.__new__(cls, *args, **kwargs)
            raise NotInstantiable(f"{cls.__name__} cannot be instantiated directly")
        
        def __getattribute__(self, name):
            # this masks some attributes for child instances in the same way as for child classes
            if name in ["get", "iteritems", "mro", "registry"] and self._instantiable:
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
            return super(Item, self).__getattribute__(name)
        
        def __repr__(self):
            """ Custom string representation for an item. """
            return f"<{self.__class__.__name__} {self.type} at 0x{id(self):02x}>"
        
        def help(self):
            """ Returns a help message in Markdown format. """
            from tinyscript.report import Blockquote, Report, Section, Text
            md = Report()
            if getattr(self, "description", None):
                md.append(Text(self.description))
            if getattr(self, "comment", None):
                md.append(Blockquote("**Note**: " + self.comment))
            if getattr(self, "link", None):
                md.append(Blockquote("**Link**: " + self.link))
            if getattr(self, "references", None):
                md.append(Section("References"), List(*self.references, **{'ordered': True}))
            return md.md()
        
        @classmethod
        def get(cls, item, error=True):
            """ Simple class method for returning the class of an item based on its name (case-insensitive). """
            for i in cls.registry:
                if i.name == (item.name if isinstance(item, Item) else format_shortname(item, "-")):
                    return i
            if error:
                raise ValueError(f"'{item}' is not defined")
        
        @classmethod
        def iteritems(cls):
            """ Class-level iterator for returning enabled items. """
            for i in cls.registry:
                try:
                    if i.status in i.__class__._enabled:
                        yield i
                except AttributeError:
                    yield i
        
        @property
        def logger(self):
            return self.__class__.logger
        
        @property
        def source(self):
            if self._instantiable and not isinstance(self._source, str):
                self.__class__._source = ""
            return self.__class__._source
    return Item
lazy_load_object("Item", _init_item)


def load_yaml_config(cfg, no_defaults=(), parse_defaults=True):
    """ Load a YAML configuration, either as a single file or a folder with YAML files (in this case, loading
         defaults.yml in priority to get the defaults first). """
    from copy import deepcopy
    # local function for expanding defaults from child configurations
    def _set(config, defaults_=None):
        if parse_defaults:
            defaults = deepcopy(defaults_ or {})
            defaults.update(config.pop('defaults', {}))
            for params in [v for v in config.values()]:
                for default, value in defaults.items():
                    if default in no_defaults:
                        raise ValueError(f"default value for parameter '{default}' is not allowed")
                    if isinstance(value, dict):
                        # example advanced defaults configuration:
                        #   defaults:
                        #     keep:
                        #       match:
                        #         - ^entropy*   (do not keep entropy-based features)
                        #         - ^is_*       (do not keep boolean features)
                        #       value: false
                        value.pop('comment', None)
                        if set(value.keys()) == {'match', 'value'}:
                            #raise ValueError(f"Bad configuration for default '{default}' ; should be a dictionary with"
                            #                 " 'value' and 'match' (format: list) as its keys")
                            v = value['value']
                            for pattern in value['match']:
                                for name2 in config.keys():
                                    # special case: boolean value ; in this case, set value for matching names and set
                                    #                                non-matching names to its opposite
                                    if isinstance(v, bool):
                                        # in the advanced example here above, entropy-based and boolean features will
                                        #  not be kept but all others will be
                                        config[name2].setdefault(default, v if re.search(pattern, name2) else not v)
                                        # this means that, if we want to keep additional features, we can still force
                                        #  keep=true per feature declaration
                                    elif re.search(pattern, name2):
                                        config[name2].setdefault(default, v)
                    else:
                        # example normal defaults configuration:
                        #   defaults:
                        #     keep:   false
                        #     source: <unknown>
                        params.setdefault(default, value)
        return config
    # local function for merging child configurations
    def _merge(config, k, v, keep=False):
        if k not in config.keys():
            config[k] = deepcopy(v)
        else:
            v1 = config[k]
            if isinstance(v1, dict):
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        _merge(v1, sk, sv)
                elif not keep:
                    config[k] = v
            elif isinstance(v1, (list, set, tuple)):
                if isinstance(v, (list, set, tuple)):
                    for sv in v:
                        if sv not in v1:
                            v1.append(sv)
                else:
                    v1.append(v)
            elif not keep:
                config[k] = v
    # local function for updating the main configuration
    def _update(base, folder, defaults, parent=()):
        defaults_, dflt = deepcopy(defaults), folder.joinpath("defaults.yml")
        if dflt.is_file():
            with dflt.open() as f:
                defaults_.update(yaml.load(f, Loader=yaml.Loader) or {})
        for cfg in folder.listdir(lambda p: p.extension == ".yml"):
            if cfg.stem == "defaults":
                continue
            with cfg.open() as f:
                config = yaml.load(f, Loader=yaml.Loader) or {}
            _set(config, defaults_)
            for k, v in config.items():
                _merge(base, k, v)
        for cfg_folder in folder.listdir(lambda p: p.is_dir()):
            _update(base, cfg_folder, defaults_, parent)
    # get the list of configs ; may be:
    #  - single YAML file
    #  - folder (and subfolders) with YAML files
    p = cfg if isinstance(cfg, Path) else Path(config[cfg])
    if p.is_dir():
        # parse YAML configurations according to the following rules:
        #  - when encountering a defaults.yml, its values become the defautls for the current folder and subfolders
        #  - when finding a defaults key in a .yml, these default values are merged with higher level defaults
        d = {}
        _update(d, p, {})
    else:
        with p.open() as f:
            d = _set(yaml.load(f, Loader=yaml.Loader) or {})
    # collect properties that are applicable for all the other features
    for name, params in d.items():
        yield name, params


def save_yaml_config(obj, cfg):
    """ Save a YAM configuration. """
    #TODO

