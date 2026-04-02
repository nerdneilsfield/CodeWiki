import re

content = r"""
\begin{align}
\end{align}
\begin{pmatrix}
\end{pmatrix}
"""
begins = re.findall(r"\\begin\{([^}]+)\}", content)
ends = re.findall(r"\\end\{([^}]+)\}", content)
print(f"{begins=}")
print(f"{ends=}")
print(f"{begins != ends=}")
