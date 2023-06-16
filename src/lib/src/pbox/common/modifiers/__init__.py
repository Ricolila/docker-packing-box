# -*- coding: UTF-8 -*-
import yaml
from tinyscript import logging, re
import lief
from .__common__ import *
from .__common__ import __all__ as __common__
from .elf import *
from .elf import __all__ as __elf__
from .macho import *
from .macho import __all__ as __macho__
from .pe import *
from .pe import __all__ as __pe__
from ...common.config import config
from ...common.utils import dict2, expand_formats, FORMATS

__all__ = ["Modifiers"]


class Modifier(dict2):
    _fields = {'apply': True, 'loop': None, 'grid': None}
    def __call__(self, d, parser=None, **kw):
        if self.loop is not None:
            for _ in range(self.loop):
                parser = super().__call__(d, parser=parser, **kw)
            return parser
        else:
            return super().__call__(d, parser=parser, **kw)


class Modifiers(list):
    """ This class parses the YAML definitions of modifiers to be applied to executables for alterations.
         It works as a list that contains the names of alterations applied to the executable given in input. """
    registry = None
    source   = config['modifiers']
    
    @logging.bindLogger
    def __init__(self, exe, select=None, warn=False):
        # parse YAML modifiers definition once
        if Modifiers.registry is None:
            # open the target YAML-formatted modifiers set only once
            with open(Modifiers.source) as f:
                modifiers = yaml.load(f, Loader=yaml.Loader) or {}
            Modifiers.registry = {}
            # collect properties that are applicable for all the modifiers
            data_all = modifiers.pop('defaults', {})
            for name, params in modifiers.items():
                for i in data_all.items():
                    params.setdefault(*i)
                r = params.pop('result', {})
                # consider most specific modifiers first, then those for intermediate format classes and finally the
                #  collapsed class "All"
                for clist in [expand_formats("All"), list(FORMATS.keys())[1:], ["All"]]:
                    for c in clist:
                        expr = r.get(c) if isinstance(r, dict) else str(r)
                        if expr:
                            m = Modifier(params, name=name, parent=self, result=expr)
                            for c2 in expand_formats(c):
                                Modifiers.registry.setdefault(c2, {})
                                Modifiers.registry[c2][m.name] = m
        # check the list of selected modifiers if relevant, and filter out bad names (if warn=True)
        for name in (select or [])[:]:
            if name not in Modifiers.registry[exe.format]:
                msg = "Modifier '%s' does not exist" % name
                if warn:
                    self.logger.warning(msg)
                    select.remove(name)
                else:
                    raise ValueError(msg)
        if exe is not None:
            parser = None
            for name, modifier in Modifiers.registry[exe.format].items():
                if select is None and not modifier.apply or select is not None and name not in select:
                    continue
                
                d = {}
                if parser is None:
                    tmp_parser = lief.parse(str(exe.destination))
                    d.update(sections=list(tmp_parser.sections))
                else:
                    d.update(sections=list(parser.get_sections()),
                             compute_checksum=parser.compute_checksum)
                
                d.update({k: globals()[k] for k in __common__})
                md = __elf__ if exe.format in expand_formats("ELF") else \
                     __macho__ if exe.format in expand_formats("Mach-O") else\
                     __pe__ if exe.format in expand_formats("PE") else []
                d.update({k: globals()[k] for k in md})
                d.update(Modifiers.registry[exe.format])
                
                kw = {'executable': exe, 'parser': parser}
                try:
                    parser = modifier(d, **kw)
                    self.append(name)
                except Exception as e:
                    self.logger.warning("%s: %s" % (name, str(e)))
            
            if parser is not None:
                parser.build()    

    @staticmethod
    def names(format="All"):
        Modifiers(None)  # force registry initialization
        l = []
        for c in expand_formats(format):
            l.extend(list(Modifiers.registry[c].keys()))
        return sorted(list(set(l)))
