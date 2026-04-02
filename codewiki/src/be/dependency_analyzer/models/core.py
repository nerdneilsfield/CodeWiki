from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Set
from datetime import datetime


class ParamInfo(BaseModel):
    name: str
    type_str: Optional[str] = None
    is_pointer: bool = False
    is_reference: bool = False
    is_const: bool = False


class DataFlowEdge(BaseModel):
    param_name: str
    param_type: Optional[str] = None
    direction: str = "in"  # "in" | "out" | "inout"
    ownership: Optional[str] = None  # "transfer" | "borrow" | "shared" | "copy"
    lifetime_hint: Optional[str] = None  # "caller_scope" | "callee_owns" | "static" | "heap"


class HLSPragma(BaseModel):
    pragma_type: str
    params: Dict[str, str] = {}
    target: Optional[str] = None
    line: int
    hardware_semantic: str = ""


class Node(BaseModel):
    id: str

    name: str

    component_type: str

    file_path: str

    relative_path: str

    depends_on: Set[str] = set()

    source_code: Optional[str] = None

    start_line: int = 0

    end_line: int = 0

    has_docstring: bool = False

    docstring: str = ""

    parameters: Optional[List[str]] = None

    node_type: Optional[str] = None

    base_classes: Optional[List[str]] = None

    class_name: Optional[str] = None

    display_name: Optional[str] = None

    component_id: Optional[str] = None

    hls_pragmas: Optional[List["HLSPragma"]] = None

    is_hls_kernel: bool = False

    def get_display_name(self) -> str:
        return self.display_name or self.name


class CallRelationship(BaseModel):
    caller: str

    callee: str

    call_line: Optional[int] = None

    is_resolved: bool = False

    relationship_type: Optional[str] = None

    data_flow: Optional[List[DataFlowEdge]] = None


class Repository(BaseModel):
    url: str

    name: str

    clone_path: str

    analysis_id: str
