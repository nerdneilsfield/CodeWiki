"""
Post-generation instructions generator.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _emit_lines(*lines: str) -> None:
    for line in lines:
        logger.info(line)


def compute_github_pages_url(repo_url: str, repo_name: str) -> str:
    """
    Compute expected GitHub Pages URL from repository URL.

    Args:
        repo_url: GitHub repository URL
        repo_name: Repository name

    Returns:
        Expected GitHub Pages URL
    """
    # Extract owner from GitHub URL
    # e.g., "https://github.com/owner/repo" -> "owner"
    if "github.com" in repo_url:
        parts = repo_url.rstrip("/").split("/")
        if len(parts) >= 2:
            owner = parts[-2]
            repo = parts[-1].replace(".git", "")
            return f"https://{owner}.github.io/{repo}/"

    return f"https://YOUR_USERNAME.github.io/{repo_name}/"


def get_pr_creation_url(repo_url: str, branch_name: str) -> str:
    """
    Get PR creation URL for GitHub.

    Args:
        repo_url: GitHub repository URL
        branch_name: Branch name

    Returns:
        PR creation URL
    """
    base_url = repo_url.rstrip("/").replace(".git", "")
    return f"{base_url}/compare/{branch_name}"


def display_post_generation_instructions(
    output_dir: Path,
    repo_name: str,
    repo_url: Optional[str] = None,
    branch_name: Optional[str] = None,
    github_pages: bool = False,
    files_generated: Optional[list] = None,
    statistics: Optional[dict] = None,
):
    """
    Display post-generation instructions.

    Args:
        output_dir: Output directory path
        repo_name: Repository name
        repo_url: GitHub repository URL (optional)
        branch_name: Git branch name (optional)
        github_pages: Whether GitHub Pages HTML was generated
        files_generated: List of generated files
        statistics: Generation statistics
    """
    logger.info("Documentation generated successfully")

    # Output directory
    _emit_lines("Output directory:", f"  {output_dir}")

    # Generated files
    if files_generated:
        _emit_lines("Generated files:")
        for file in files_generated[:10]:  # Show first 10
            _emit_lines(f"  - {file}")
        if len(files_generated) > 10:
            _emit_lines(f"  ... and {len(files_generated) - 10} more")

    # Statistics
    if statistics:
        _emit_lines("Statistics:")
        if "module_count" in statistics:
            _emit_lines(f"  Total modules:     {statistics['module_count']}")
        if "total_files_analyzed" in statistics:
            _emit_lines(f"  Files analyzed:    {statistics['total_files_analyzed']}")
        if "generation_time" in statistics:
            minutes = int(statistics["generation_time"] // 60)
            seconds = int(statistics["generation_time"] % 60)
            _emit_lines(f"  Generation time:   {minutes} minutes {seconds} seconds")
        # if 'total_tokens_used' in statistics:
        #     tokens = statistics['total_tokens_used']
        #     _emit_lines(f"  Tokens used:       ~{tokens:,}")

    # Next steps
    _emit_lines("Next steps:")

    _emit_lines("1. Review the generated documentation:", f"   cat {output_dir}/overview.md")
    if github_pages:
        _emit_lines(f"   open {output_dir}/index.html  # View in browser")

    if branch_name:
        # Git workflow with branch
        _emit_lines("2. Push the documentation branch:", f"   git push origin {branch_name}")

        if repo_url:
            pr_url = get_pr_creation_url(repo_url, branch_name)
            _emit_lines("3. Create a Pull Request to merge documentation:", f"   {pr_url}")

            _emit_lines("4. After merge, enable GitHub Pages:")
        else:
            _emit_lines("3. Enable GitHub Pages:")
    else:
        # Direct commit workflow
        _emit_lines(
            "2. Commit the documentation:",
            "   git add docs/",
            '   git commit -m "Add generated documentation"',
        )

        _emit_lines("3. Push to GitHub:", "   git push origin main")

        _emit_lines("4. Enable GitHub Pages:")

    _emit_lines(
        "   - Go to repository Settings → Pages",
        "   - Source: Deploy from a branch",
        "   - Branch: main, folder: /docs",
    )

    if repo_url:
        github_pages_url = compute_github_pages_url(repo_url, repo_name)
        _emit_lines("5. Your documentation will be available at:", f"   {github_pages_url}")


def display_generation_summary(
    success: bool, error_message: Optional[str] = None, output_dir: Optional[Path] = None
):
    """
    Display generation summary (success or failure).

    Args:
        success: Whether generation was successful
        error_message: Error message if failed
        output_dir: Output directory if successful
    """
    if success:
        logger.info("Generation completed successfully")
        if output_dir:
            logger.info("Documentation saved to: %s", output_dir)
    else:
        logger.error("Generation failed")
        if error_message:
            for line in error_message.splitlines():
                logger.error(line)
