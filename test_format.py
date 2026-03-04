_MATH_REPAIR_USER = """\
You are a LaTeX math expert. Fix the supplied formula so it parses without errors.
Issues:
{issues}

Formula to fix:
{formula}
"""

content = "\\begin{align} x \\end{align}"

prompt = _MATH_REPAIR_USER.format(
    issues="Test",
    formula=content.strip(),
)
print(prompt)
