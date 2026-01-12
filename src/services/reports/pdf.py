"""PDF generation from markdown using Pandoc."""

import asyncio
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class PDFConversionError(Exception):
    """Raised when PDF conversion fails."""

    pass


class PDFConverter:
    """Convert markdown to PDF using Pandoc."""

    def __init__(
        self,
        pandoc_path: str = "pandoc",
        pdf_engine: str = "xelatex",
    ):
        """Initialize PDF converter.

        Args:
            pandoc_path: Path to pandoc executable.
            pdf_engine: PDF engine (xelatex, pdflatex, etc.).
        """
        self.pandoc_path = pandoc_path
        self.pdf_engine = pdf_engine

    async def convert(
        self,
        markdown: str,
        title: str | None = None,
    ) -> bytes:
        """Convert markdown content to PDF.

        Args:
            markdown: Markdown content to convert.
            title: Optional document title.

        Returns:
            PDF file contents as bytes.

        Raises:
            PDFConversionError: If conversion fails.
        """
        # Create temp files for input/output
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as md_file:
            md_file.write(markdown)
            md_path = md_file.name

        pdf_path = md_path.replace(".md", ".pdf")

        try:
            # Build pandoc command
            cmd = [
                self.pandoc_path,
                md_path,
                "-o",
                pdf_path,
                f"--pdf-engine={self.pdf_engine}",
                "--standalone",
            ]

            if title:
                cmd.extend(["--metadata", f"title={title}"])

            # Add styling options
            cmd.extend([
                "--variable", "geometry:margin=1in",
                "--variable", "fontsize=11pt",
            ])

            logger.debug("pdf_conversion_started", command=" ".join(cmd))

            # Run pandoc
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(
                    "pdf_conversion_failed",
                    returncode=process.returncode,
                    error=error_msg,
                )
                raise PDFConversionError(f"Pandoc failed: {error_msg}")

            # Read generated PDF
            pdf_content = Path(pdf_path).read_bytes()

            logger.info(
                "pdf_conversion_completed",
                input_size=len(markdown),
                output_size=len(pdf_content),
            )

            return pdf_content

        finally:
            # Cleanup temp files
            Path(md_path).unlink(missing_ok=True)
            Path(pdf_path).unlink(missing_ok=True)

    async def is_available(self) -> bool:
        """Check if Pandoc is available.

        Returns:
            True if pandoc is installed and working.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self.pandoc_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False
