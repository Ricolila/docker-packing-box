# -*- coding: UTF-8 -*-
from tinyscript import logging, re, subprocess
from tinyscript.helpers import confirm, execute_and_log as run, user_input, Path
from tinyscript.report import *

from .scenario import *
from .scenario import __all__ as _scenario
from ..dataset import *
from ..model import *
from ...helpers import *


__all__ = ["Experiment"] + _scenario


def __init():
    class Experiment(Entity):
        """ Folder structure:
        
        [name]
          +-- conf          custom YAML configuration files
          +-- (data)        custom executable format related data (e.g. common standard/packer section names)
          +-- datasets      datasets specific to the experiment
          +-- models        models specific to the experiment
          +-- (figures)     figures generated with visualization tools
          +-- (scripts)     additional scripts
          +-- commands.rc   commands for replaying experiment
          +-- README.md     notes for explaining the experiment
        """
        STRUCTURE = ["conf", "datasets", "models", "commands.rc*", "README.md", "data*", "figures*", "scripts*"]
        
        def __new__(cls, name="experiment", **kw):
            self = super(Experiment, cls).__new__(cls, name, **kw)
            if self.path and not self.path.is_under(config['experiments'].absolute()):
                config['experiments'] = self.path.dirname
            return self
        
        def __getitem__(self, name):
            """ Get something from the experiment folder, either a config file, a dataset or a model.
            
            NB: In the case of YAML configuration files, this method aims to return the actually used YAML, not
                 specifically the one from the experiment (if exists) ; therefore, when a YAML has not been edited
                 within the scope of the experiment yet, this method will return the YAML from the main workspace.
            """
            # case 1: 'name' is README(.md) or commands(.rc) ; return a Path instance
            if name in ["README", "README.md"]:
                return self.path.joinpath("README.md")
            if name in ["commands", "commands.rc"]:
                return self.path.joinpath("commands.rc")
            # case 2: 'name' matches a reserved word for a YAML configuration file ; return a Path instance
            #          get it either from the main workspace or, if existing, from the experiment
            if name in config._defaults['definitions'].keys():
                conf = self.path.joinpath("conf").joinpath(name + ".yml")
                if not conf.exists():
                    conf = config[name]
                return conf
            # case 3: 'name' matches a dataset from the experiment ; return a (Fileless)Dataset instance
            for ds in self.path.joinpath("datasets").listdir(Dataset.check):
                if name == ds.stem:
                    return Dataset.load(ds)
            # case 4: 'name' matches a model from the experiment ; return a (Dumped)Model instance
            for md in self.path.joinpath("models").listdir(Model.check):
                if name == md.stem:
                    return Model.load(md)
            raise KeyError(name)
        
        def __len__(self):
            """ Get dataset's length. """
            return Dataset.count() + Model.count()
        
        def _import(self, subcommand=None, path=None, **kw):
            """ Import an item (among configs, data and scripts) to the experiment. """
            _confirm = lambda p: not p.exists() or confirm(f"Are you sure you want to overwrite '{p}' ?")
            l, p_src = Experiment.logger, Path(path)
            if not p_src.exists():
                l.error(f"'{p_src}' is not a item for import")
                return
            if subcommand == "config":
                p_exp = self.path.joinpath("conf").joinpath(p_src.basename)
                try:
                    if not p_src.extension == ".yml":
                        raise KeyError
                    config.get(p_src.stem, sections="definitions", error=True)
                    if not p_src.is_samepath(p_exp) and _confirm(p_exp):
                        l.debug(f"copying configuration file{['', 's'][p_src.is_dir()]} from '{p_src}'...")
                        p_src.copy(p_exp)
                except KeyError:
                    l.error(f"'{p_src}' is not a valid configuration name")
            elif subcommand == "data":
                fmt, d = kw.get('format', "All"), self.path.joinpath("data")
                if fmt == "All":
                    p_exp = d.joinpath(p_src.basename)
                else:
                    p_exp = d.joinpath(format_shortname(fmt))
                    p_exp.mkdir(parents=True, exist_ok=True)
                    if fmt in FORMATS['All']:
                        p_exp = p_exp.joinpath(p_src.basename)
                    elif fmt in expand_formats("All"):
                        p_exp = p_exp.joinpath(f"{p_exp.stem}_{format_shortname(fmt)}{p_exp.extension}")
                if _confirm(p_exp):
                    p_src.copy(p_exp)
            elif subcommand == "script":
                scripts = self.path.joinpath("scripts")
                scripts.mkdir(parents=True, exist_ok=True)
                p_exp = scripts.joinpath(p_src.basename)
                if _confirm(p_exp):
                    p_src.copy(p_exp).chmod(0o744)
            else:
                raise ValueError("Bad subcommand (should be one of: config|data|script)")
        
        def _load(self):
            try:
                for folder in ["conf", "datasets", "models"]:
                    folder = self.path.joinpath(folder)
                    if not folder.exists():
                        folder.mkdir()
                self['README'].touch()
                config['experiment'] = config['workspace'] = self.path
            except PermissionError as e:
                Experiment.logger.exception(e)
        
        def close(self, **kw):
            """ Close the currently open experiment. """
            del config['autocommit']
            del config['experiment']
        
        def commit(self, command=None, force=False, silent=False, **kw):
            """ Commit the latest executed OS command to the resource file (.rc). """
            l = Experiment.logger
            rc = self['commands']
            rc.touch()
            rc_last_line = ""
            for rc_last_line in rc.read_lines(encoding="utf-8", reverse=True):
                pass
            if rc_last_line:
                l.debug(f"last command: {rc_last_line}")
            hist_last_line = command or ""
            if hist_last_line != "":
                for hist_last_line in Path("~/.bash_history", expand=True).read_lines(encoding="utf-8", reverse=True):
                    if any(hist_last_line.startswith(cmd + " ") for cmd in COMMIT_VALID_COMMANDS) and \
                       all(o not in hist_last_line.split() for o in ["-h", "--help"]):
                        break
            if hist_last_line == "" or hist_last_line == rc_last_line:
                getattr(l, ["warning", "debug"][silent])("Nothing to commit")
            elif force or confirm(f"Do you really want to commit this command: {hist_last_line} ?"):
                l.debug(f"writing command '{hist_last_line}' to commands.rc...")
                with rc.open('a') as f:
                    f.write(hist_last_line)
        
        def compress(self, **kw):
            """ Compress the experiment by converting all datasets to fileless datasets. """
            l, done = Experiment.logger, False
            for dset in Path(config['datasets']).listdir(Dataset.check):
                l.info(f"Dataset: {dset}")
                Dataset.load(dset).convert()
                done = True
            if not done:
                l.warning("No dataset to be converted")
        
        def edit(self, force=False, **kw):
            """ Edit the README or a YAML configuration file. """
            l, conf = Experiment.logger, kw.get('config')
            try:
                p = self[conf] # can be README.md, commands.rc or YAML config files
            except KeyError:
                if force:
                    p = self.path.joinpath("conf").joinpath(conf + ".yml" if not conf.endswith(".yml") else conf)
                    edit_file(p, text=True, logger=l)
                    return
                raise
            try:
                p_main, p_exp = config[conf], self.path.joinpath("conf").joinpath(conf + ".yml")
                self._import("config", p_main, error=True)
                if p_exp.is_file():
                    l.debug(f"editing experiment's {conf} configuration...")
                    edit_file(p_exp, text=True, logger=l)
                elif p_exp.is_dir():
                    choices = [p.stem for p in p_exp.listdir(lambda x: x.extension == ".yml")]
                    stem = user_input(choices=choices, default=choices[0], required=True)
                    edit_file(p_exp.joinpath(stem + ".yml"), text=True, logger=l)
            except KeyError:
                l.debug(f"editing experiment's {p.basename}...")
                edit_file(p, text=True, logger=l)
        
        def open(self, **kw):
            """ Open the current experiment, validating its structure. """
            Experiment(self.path, load=True)  # ensures that, if the current experiment is not loaded yet (constructor
                                              #  'load' argument defaults to False), it create a blank structure if
                                              #  hence needed, no validation required (i.e. using Experiment.load(...))
        
        def play(self, scenario=None, **kw):
            """ Play a scenario. """
            l = [s for s in Scenario.registry if s.name == scenario]
            if len(l) == 0:
                self.logger.error("Scenario does not exist")
                return
            l[0].run(experiment=self, **kw)
        
        def remove(self, subcommand=None, path=None, **kw):
            """ Remove an item (among configs, data and scripts) from the experiment. """
            l, folder = Experiment.logger, {'config': "conf", 'script': "scripts"}.get(subcommand, subcommand)
            base = self.path.joinpath(folder)
            if not base.exists():
                raise ValueError("Bad subcommand (should be one of: config|data|script)")
            p_src = base.joinpath(path)
            if not p_src.exists():
                l.error(f"'{p_src}' is not a valid item for removal")
                return
            p_src.remove()
            if f"{folder}*" in self.STRUCTURE and sum(1 for _ in base.walk(filter_func=Path.is_file)) == 0:
                base.remove()
        
        def replay(self, stop=True, **kw):
            """ Replay registered commands (useful when a change in pbox or configuration files have been applied). """
            self.reset(**kw)
            for cmd in self.commands:
                try:
                    out, err, retc = run(cmd, **kw)
                except Exception as e:
                    if stop:
                        raise
                    self.logger.error(str(e))
        
        def reset(self, **kw):
            """ Purge datasets, models and figures. """
            Dataset.purge("all")
            Model.purge("all")
            if self.path.join("figures").exists():
                for fig in self.path.join("figures").listdir():
                    fig.remove(False)
        
        def show(self, **kw):
            """ Show an overview of the experiment. """
            from pboxtools import utils
            from textwrap import wrap
            def _tree(name):
                p = self.path.joinpath(name)
                if not p.exists():
                    return
                n = sum(1 for _ in run(f"find {p} -type f")[0].splitlines())
                if n > 0:
                    render(Section(f"{name.capitalize()} ({n})"))
                    print("")
                    for l in subprocess.run(["tree", p], check=True, capture_output=True).stdout.splitlines()[1:-1]:
                        print(l.decode())
            cfg = []
            for f in self.path.joinpath("conf").listdir():
                if f.extension != ".yml":
                    continue
                lst_func = (lambda r, s: utils.list_configfile_keys(f, r, s)) if f.stem.startswith("alteration") else \
                           getattr(utils, f'list_all_{f.stem}', lambda r, s: utils.list_configfile_keys(f, r, s))
                lst = lst_func(True, not f.stem.startswith("alteration"))
                cfg.append([f"{f.stem} ({len(lst)})", "\n".join(wrap(", ".join(lst)))])
            if len(cfg) > 0:
                render(Section(f"Configurations ({len(cfg)})"), Table(cfg, column_headers=["Name", "Entries"]))
            _tree("data")
            Dataset.list()
            Model.list()
            _tree("scripts")
        
        @property
        def commands(self):
            with self['commands'].open() as f:
                return [l.strip() for l in f if l.strip() != "" or not l.strip().startswith("#")]
        
        @property
        def configs(self):
            return [p.stem for p in self.path.joinpath("conf").listdir(lambda p: p.extension == ".yml")]
        
        @property
        def datasets(self):
            return Dataset.names
        
        @property
        def models(self):
            return Model.names
        
        @classmethod
        def list(cls, **kw):
            """ List all valid experiment folders. """
            data, headers = [], ["Name", "#Datasets", "#Models", "Custom configs"]
            for folder in config['experiments'].listdir(Experiment.check):
                exp = Experiment(folder, load=False)
                cfg = [f.stem for f in exp.path.joinpath("conf").listdir(Path.is_file) if f.extension == ".yml"]
                data.append([folder.basename, Dataset.count(), Model.count(), ", ".join(cfg)])
            if len(data) > 0:
                render(*[Section(f"Experiments ({len(data)})"), Table(data, column_headers=headers)])
            else:
                cls.logger.warning(f"No experiment found in the workspace ({config['experiments']})")
        
        @classmethod
        def validate(cls, folder, strict=False, empty=False, **kw):
            p = super(Experiment, cls).validate(folder, strict, empty, **kw)
            for cfg in p.joinpath("conf").listdir():
                if strict and cfg.stem not in config._defaults['definitions'].keys() or cfg.extension != ".yml":
                    raise StructuralError(f"'{p}' has unknown configuration file '{cfg}'")
            return p
    
    logging.setLogger("experiment")
    return Experiment
Experiment = lazy_object(__init)

