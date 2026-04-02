import re

content = r"""
\begin{align}
  \begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}
\end{align}
"""
begins = re.findall(r"\\begin\{([^}]+)\}", content)
ends = re.findall(r"\\end\{([^}]+)\}", content)
print(f"{begins=}")
print(f"{ends=}")
print(f"{begins != ends=}")
