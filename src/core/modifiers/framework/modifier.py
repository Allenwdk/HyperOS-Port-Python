"""Framework-level modifications (smali patching)."""

from __future__ import annotations

import concurrent.futures
from typing import TYPE_CHECKING

from src.core.modifiers.framework.patches import (
    INVOKE_TRUE,
    PRELOADS_SHAREDUIDS,
    REMAKE_VOID,
    RETRUN_FALSE,
    RETRUN_TRUE,
)
from src.core.modifiers.framework.tasks import FrameworkTasks

if TYPE_CHECKING:
    from src.core.context import PortingContext


class FrameworkModifier(FrameworkTasks):
    """Handles framework-level modifications (smali patching).

    This class orchestrates the modification of framework JARs including:
    - miui-services.jar modifications for EU ROM compatibility
    - services.jar modifications for signature verification bypass
    - framework.jar modifications for PropsHook, PIF injection, and signature bypass
    - Xiaomi.eu Toolbox injection
    """

    def __init__(self, context: PortingContext) -> None:
        super().__init__(context)
        # Re-export patches as instance attributes for backward compatibility
        self.RETRUN_TRUE = RETRUN_TRUE
        self.RETRUN_FALSE = RETRUN_FALSE
        self.REMAKE_VOID = REMAKE_VOID
        self.INVOKE_TRUE = INVOKE_TRUE
        self.PRELOADS_SHAREDUIDS = PRELOADS_SHAREDUIDS

    def run(self) -> None:
        """Execute all framework modifications."""
        self.logger.info("Starting Framework Modification...")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            futures.append(executor.submit(self._mod_miui_services))
            futures.append(executor.submit(self._mod_services))
            futures.append(executor.submit(self._mod_framework))

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Framework modification failed: {e}")

        self._cleanup_boot_images()
        self._inject_xeu_toolbox()
        self.logger.info("Framework Modification Completed.")

    def _cleanup_boot_images(self) -> None:
        """Delete stale boot image files after JAR modifications.

        When framework.jar / services.jar / miui-services.jar are modified (e.g.
        replaced with Port ROM versions), the pre-compiled boot image files
        (boot*.oat, boot*.art, boot*.vdex, boot*.prof) become invalid because
        they were compiled from the Stock ROM's JARs with a different dex count
        or checksum. Keeping them causes zygote64 to crash during preloadClasses
        (boot image verification failure → SIGABRT → boot loop).

        Deleting these files forces the system to recompile them on first boot
        via dex2oat, matching the current JAR contents.
        """
        system_dir = self.ctx.target_dir / "system" / "system" / "framework"
        if not system_dir.exists():
            return

        boot_image_extensions = {".oat", ".art", ".vdex", ".prof"}
        boot_image_prefixes = ["boot", "boot-framework", "boot-services"]
        deleted_count = 0

        # Search in arm64/ subdirectory (primary architecture) and root framework dir
        search_dirs = [system_dir / "arm64", system_dir]
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for f in search_dir.iterdir():
                if not f.is_file():
                    continue
                stem = f.stem
                ext = f.suffix.lower()
                if ext in boot_image_extensions and any(
                    stem == prefix or stem.startswith(prefix + ".")
                    for prefix in boot_image_prefixes
                ):
                    self.logger.info(f"Removing stale boot image: {f.relative_to(self.ctx.target_dir)}")
                    f.unlink()
                    deleted_count += 1

        if deleted_count > 0:
            self.logger.info(
                f"Removed {deleted_count} stale boot image file(s). "
                "System will recompile on first boot (slower initial startup)."
            )
        else:
            self.logger.debug("No stale boot image files found to remove.")
