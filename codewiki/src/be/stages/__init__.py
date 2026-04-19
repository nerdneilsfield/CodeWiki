from codewiki.src.be.stages.clustering import ClusteringStage
from codewiki.src.be.stages.graph_build import GraphBuildStage
from codewiki.src.be.stages.guide import GuideStage
from codewiki.src.be.stages.index_build import IndexBuildStage
from codewiki.src.be.stages.metadata import MetadataStage
from codewiki.src.be.stages.module_generation import ModuleGenerationStage
from codewiki.src.be.stages.postprocess import PostprocessStage
from codewiki.src.be.stages.state_init import StateInitStage
from codewiki.src.be.stages.tree_refinement import TreeRefinementStage

DEFAULT_STAGES = [
    GraphBuildStage(),
    IndexBuildStage(),
    ClusteringStage(),
    TreeRefinementStage(),
    StateInitStage(),
    ModuleGenerationStage(),
    GuideStage(),
    PostprocessStage(),
    MetadataStage(),
]
