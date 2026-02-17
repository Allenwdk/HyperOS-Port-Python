import argparse
import json
import logging
import shutil
import zipfile
import sys
from pathlib import Path

# Add project root to sys.path to allow imports
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from src.core.rom import RomPackage
from src.utils.shell import ShellRunner

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

def main():
    setup_logging()
    logger = logging.getLogger("BundleGen")
    
    parser = argparse.ArgumentParser(description="HyperOS EU Localization Bundle Generator")
    parser.add_argument("--rom", required=True, help="Path to Source ROM (e.g., CN ROM payload.bin/zip)")
    parser.add_argument("--config", required=True, help="Path to JSON config defining apps to extract")
    parser.add_argument("--version", default="1.0", help="Version tag for the bundle")
    parser.add_argument("--out", default=".", help="Output directory")
    args = parser.parse_args()

    work_dir = Path("build_bundle_temp").resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    try:
        # 1. Load Config
        with open(args.config, 'r') as f:
            config = json.load(f)
        
        apps_list = config.get("apps", [])
        if not apps_list:
            logger.error("No apps defined in config file.")
            return

        # 2. Extract ROM
        logger.info(f"Extracting Source ROM: {args.rom}")
        rom = RomPackage(args.rom, work_dir / "rom_extract", label="Source")
        
        # Determine which partitions we need to mount/extract based on config
        # Simply extract common system partitions to be safe
        partitions_to_extract = ["system", "product", "system_ext", "mi_ext"]
        rom.extract_images(partitions_to_extract)

        # 3. Harvest Apps
        bundle_root = work_dir / "bundle_root"
        bundle_root.mkdir(parents=True)
        
        extracted_root = rom.extracted_dir
        
        count = 0
        for app_path_str in apps_list:
            # app_path_str e.g. "product/app/MiuiCamera"
            # We need to find this in the extracted ROM
            
            # Logic: Split path into partition and relative path
            # e.g. "system/app/Foo" -> part="system", rest="app/Foo"
            parts = Path(app_path_str).parts
            if not parts: continue
            
            partition = parts[0] # system, product, etc.
            relative_path = Path(*parts[1:])
            
            # Search candidate paths
            candidates = [
                extracted_root / app_path_str,                         # Standard: extracted/system/app/Foo
                extracted_root / partition / partition / relative_path # SAR: extracted/system/system/app/Foo
            ]
            
            found_src = None
            for candidate in candidates:
                if candidate.exists():
                    found_src = candidate
                    break
            
            if not found_src:
                logger.warning(f"App not found: {app_path_str}")
                continue
                
            # 2. Copy to bundle root, preserving structure
            # Always map to the simple structure (bundle_root/system/app/Foo)
            dest_path = bundle_root / app_path_str
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            if found_src.is_dir():
                if dest_path.exists(): shutil.rmtree(dest_path)
                shutil.copytree(found_src, dest_path)
            else:
                shutil.copy2(found_src, dest_path)
            
            logger.info(f"Collected: {app_path_str}")
            count += 1

        if count == 0:
            logger.error("No apps collected! Check your config and ROM.")
            return

        # 4. Pack Bundle
        out_name = f"eu_localization_bundle_v{args.version}.zip"
        out_path = Path(args.out).resolve() / out_name
        
        logger.info(f"Zipping bundle to {out_path}...")
        
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in bundle_root.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(bundle_root)
                    zf.write(file_path, arcname)
        
        logger.info("Done!")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        # Cleanup
        if work_dir.exists():
            shutil.rmtree(work_dir)

if __name__ == "__main__":
    main()
