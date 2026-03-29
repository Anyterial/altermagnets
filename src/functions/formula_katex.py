import re

SUBSCRIPT_PATTERN = re.compile(r"(?<=[A-Za-z)\]])(\d+(?:\.\d+)?)")


def _escape_latex(value: str) -> str:
    escaped = value.replace("\\", r"\\")
    escaped = escaped.replace("{", r"\{")
    escaped = escaped.replace("}", r"\}")
    escaped = escaped.replace("%", r"\%")
    escaped = escaped.replace("&", r"\&")
    escaped = escaped.replace("#", r"\#")
    escaped = escaped.replace("$", r"\$")
    escaped = escaped.replace("_", r"\_")
    return escaped


def katex_formula_inline(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "$" in text:
        return text

    latex = _escape_latex(text)
    latex = latex.replace("·", r"\cdot ")
    latex = latex.replace("⋅", r"\cdot ")
    latex = SUBSCRIPT_PATTERN.sub(r"_{\1}", latex)
    return f"$\\mathrm{{{latex}}}$"
