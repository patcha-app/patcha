from pathlib import Path
from jinja2 import Environment, FileSystemLoader

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    keep_trailing_newline=True,
)


def render_system() -> str:
    return _env.get_template("system.j2").render().strip()


def render_user(macro_name: str, **kwargs) -> str:
    module = _env.get_template("user.j2").make_module()
    return str(getattr(module, macro_name)(**kwargs)).strip()
