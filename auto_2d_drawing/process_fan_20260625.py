"""
Batch process FAN 20260625 source data while preserving folder structure.

Source:
  F:\\School\\力致\\new_data\\Maildir\\FCN\\FAN\\20260625

Output:
  output\\fan_20260625_autodraw\\...

For each source folder:
  - STEP/STP files are converted through the existing auto annotation flow.
  - DWG files are converted by ODA File Converter to DXF, then rendered to SVG/PDF.

The script is resumable: existing generated files are skipped.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from batch_generate import generate_single
from config import OUTPUT_DIR


DEFAULT_SOURCE_ROOT = r"F:\School\力致\new_data\Maildir\FCN\FAN\20260625"
DEFAULT_OUTPUT_NAME = "fan_20260625_autodraw"
DWG_OUTPUT_SUBDIR = "_dwg_reference"


def main():
    parser = argparse.ArgumentParser(description="Process FAN 20260625 STEP and DWG data.")
    parser.add_argument("--source", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output", default=os.path.join(OUTPUT_DIR, DEFAULT_OUTPUT_NAME))
    parser.add_argument("--step-only", action="store_true")
    parser.add_argument("--dwg-only", action="store_true")
    parser.add_argument("--dwg-dxf-only", action="store_true", help="Convert DWG to DXF only; skip ezdxf rendering.")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even if target files exist.")
    args = parser.parse_args()

    source_root = os.path.abspath(args.source)
    output_root = os.path.abspath(args.output)
    os.makedirs(output_root, exist_ok=True)

    started = time.time()
    manifest = load_manifest(output_root)
    manifest["source_root"] = source_root
    manifest["output_root"] = output_root
    manifest["started_at"] = manifest.get("started_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    if not args.dwg_only:
        manifest["step"] = []
        manifest["errors"] = [e for e in manifest.get("errors", []) if e.get("stage") != "step"]
    if not args.step_only:
        manifest["dwg"] = []
        manifest["errors"] = [e for e in manifest.get("errors", []) if not e.get("stage", "").startswith("dwg")]

    if not args.dwg_only:
        process_step_files(source_root, output_root, manifest, force=args.force)
        write_manifest(output_root, manifest)

    if not args.step_only:
        process_dwg_files(source_root, output_root, manifest, force=args.force, dxf_only=args.dwg_dxf_only)
        write_manifest(output_root, manifest)

    manifest["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["elapsed_seconds"] = round(time.time() - started, 1)
    write_manifest(output_root, manifest)

    print("\n" + "=" * 72)
    print("Batch complete")
    print(f"STEP processed: {sum(1 for item in manifest['step'] if item['status'] == 'ok')}")
    print(f"DWG processed:  {sum(1 for item in manifest['dwg'] if item['status'] == 'ok')}")
    print(f"Errors:         {len(manifest['errors'])}")
    print(f"Output:         {output_root}")
    print("=" * 72)


def process_step_files(source_root, output_root, manifest, force=False):
    step_files = sorted(iter_files(source_root, (".stp", ".step")))
    print(f"[STEP] Found {len(step_files)} files")

    for idx, step_path in enumerate(step_files, start=1):
        rel_path = os.path.relpath(step_path, source_root)
        rel_dir = os.path.dirname(rel_path)
        target_dir = os.path.join(output_root, rel_dir)
        base_name = safe_output_base(Path(step_path).stem)
        pdf_path = os.path.join(target_dir, f"{base_name}.pdf")
        svg_path = os.path.join(target_dir, f"{base_name}.svg")
        dxf_path = os.path.join(target_dir, f"{base_name}.dxf")

        print(f"\n[STEP {idx}/{len(step_files)}] {rel_path}")
        if not force and (os.path.exists(pdf_path) or os.path.exists(svg_path)) and os.path.exists(dxf_path):
            print("  skip: output exists")
            manifest["step"].append({"source": rel_path, "status": "skipped", "output": rel_output(output_root, target_dir, base_name)})
            continue

        os.makedirs(target_dir, exist_ok=True)
        try:
            generate_single(
                step_path=step_path,
                output_dir=target_dir,
                output_name=base_name,
                part_name=Path(step_path).stem,
                drawing_no=Path(step_path).stem,
                revision="R00",
                material="---",
                model_code=Path(rel_dir).name if rel_dir else "---",
            )
            manifest["step"].append({"source": rel_path, "status": "ok", "output": rel_output(output_root, target_dir, base_name)})
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            print(f"  ERROR: {message}")
            manifest["errors"].append({"stage": "step", "source": rel_path, "error": message})
            manifest["step"].append({"source": rel_path, "status": "error", "error": message})
        write_manifest(output_root, manifest)


def process_dwg_files(source_root, output_root, manifest, force=False, dxf_only=False):
    dwg_files = sorted(iter_files(source_root, (".dwg",)))
    print(f"\n[DWG] Found {len(dwg_files)} files")
    if not dwg_files:
        return

    oda_exe = find_oda_converter()
    if not oda_exe:
        message = "ODA File Converter not found under Program Files."
        print(f"[DWG] ERROR: {message}")
        manifest["errors"].append({"stage": "dwg", "source": source_root, "error": message})
        return
    print(f"[DWG] ODA: {oda_exe}")

    grouped = {}
    for dwg_path in dwg_files:
        grouped.setdefault(os.path.dirname(dwg_path), []).append(dwg_path)

    processed = 0
    for source_dir, files in grouped.items():
        rel_dir = os.path.relpath(source_dir, source_root)
        if rel_dir == ".":
            rel_dir = ""
        target_dir = os.path.join(output_root, rel_dir, DWG_OUTPUT_SUBDIR)
        os.makedirs(target_dir, exist_ok=True)

        pending = []
        for dwg_path in files:
            base_name = Path(dwg_path).stem
            dxf_path = os.path.join(target_dir, f"{base_name}.dxf")
            svg_path = os.path.join(target_dir, f"{base_name}.svg")
            pdf_path = os.path.join(target_dir, f"{base_name}.pdf")
            is_done = os.path.exists(dxf_path) if dxf_only else os.path.exists(dxf_path) and (os.path.exists(pdf_path) or os.path.exists(svg_path))
            if force or not is_done:
                pending.append(dwg_path)
            else:
                manifest["dwg"].append({"source": os.path.relpath(dwg_path, source_root), "status": "skipped", "output": rel_output(output_root, target_dir, base_name)})

        if not pending:
            continue

        print(f"\n[DWG folder] {rel_dir or '.'} ({len(pending)} pending)")
        pending_dir = os.path.join(target_dir, "_pending_dwg")
        os.makedirs(pending_dir, exist_ok=True)
        clear_directory(pending_dir)
        for dwg_path in pending:
            shutil.copy2(dwg_path, os.path.join(pending_dir, os.path.basename(dwg_path)))

        run_oda(oda_exe, pending_dir, target_dir)
        shutil.rmtree(pending_dir, ignore_errors=True)

        for dwg_path in pending:
            processed += 1
            rel_path = os.path.relpath(dwg_path, source_root)
            base_name = Path(dwg_path).stem
            dxf_path = os.path.join(target_dir, f"{base_name}.dxf")
            print(f"  [DWG {processed}/{len(dwg_files)}] {rel_path}")

            if not os.path.exists(dxf_path):
                message = "DXF not produced by ODA"
                print(f"    ERROR: {message}")
                manifest["errors"].append({"stage": "dwg", "source": rel_path, "error": message})
                manifest["dwg"].append({"source": rel_path, "status": "error", "error": message})
                continue

            if dxf_only:
                manifest["dwg"].append({"source": rel_path, "status": "ok", "output": rel_output(output_root, target_dir, base_name)})
                continue

            try:
                render_dxf_outputs_subprocess(dxf_path)
                manifest["dwg"].append({"source": rel_path, "status": "ok", "output": rel_output(output_root, target_dir, base_name)})
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"    ERROR: {message}")
                manifest["errors"].append({"stage": "dwg_render", "source": rel_path, "error": message})
                manifest["dwg"].append({"source": rel_path, "status": "error", "error": message})
            write_manifest(output_root, manifest)


def run_oda(oda_exe, input_dir, output_dir):
    cmd = [
        oda_exe,
        input_dir,
        output_dir,
        "ACAD2018",
        "DXF",
        "0",
        "1",
        "*.dwg",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("  ODA returned non-zero; continuing with any DXF files it produced.")
        if result.stderr:
            print(result.stderr[:1200])


def render_dxf_outputs(dxf_path):
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing.svg import SVGBackend

    svg_path = os.path.splitext(dxf_path)[0] + ".svg"
    pdf_path = os.path.splitext(dxf_path)[0] + ".pdf"

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ctx = RenderContext(doc)
    backend = SVGBackend()
    Frontend(ctx, backend).draw_layout(msp, finalize=True)
    svg = backend.get_string(layout.Page(0, 0))
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)

    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run([converter, "-f", "pdf", "-o", pdf_path, svg_path], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return

    # Fallback: direct matplotlib PDF export.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def render_dxf_outputs_subprocess(dxf_path):
    worker = os.path.join(os.path.dirname(__file__), "process_fan_20260625.py")
    result = subprocess.run(
        [sys.executable, "-u", worker, "--render-dxf", dxf_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"render worker exited {result.returncode}").strip()
        raise RuntimeError(detail[:1200])


def find_oda_converter():
    candidates = glob.glob(r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe")
    candidates.extend(glob.glob(r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe"))
    return candidates[0] if candidates else None


def iter_files(root, suffixes):
    for current_root, _, files in os.walk(root):
        for filename in files:
            if filename.lower().endswith(suffixes):
                yield os.path.join(current_root, filename)


def safe_output_base(name):
    return name.replace("/", "_").replace("\\", "_").strip() or "drawing"


def rel_output(output_root, target_dir, base_name):
    rel_dir = os.path.relpath(target_dir, output_root)
    rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
    prefix = f"{rel_dir}/" if rel_dir else ""
    return {
        "base": f"{prefix}{base_name}",
        "pdf": f"{prefix}{base_name}.pdf",
        "svg": f"{prefix}{base_name}.svg",
        "dxf": f"{prefix}{base_name}.dxf",
    }


def clear_directory(path):
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)


def write_manifest(output_root, manifest):
    os.makedirs(output_root, exist_ok=True)
    with open(os.path.join(output_root, "batch_index.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def load_manifest(output_root):
    path = os.path.join(output_root, "batch_index.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "source_root": data.get("source_root", ""),
                "output_root": data.get("output_root", ""),
                "started_at": data.get("started_at"),
                "step": data.get("step", []),
                "dwg": data.get("dwg", []),
                "errors": data.get("errors", []),
            }
        except Exception:
            pass
    return {"source_root": "", "output_root": "", "started_at": None, "step": [], "dwg": [], "errors": []}


if __name__ == "__main__":
    if "--render-dxf" in sys.argv:
        index = sys.argv.index("--render-dxf")
        render_dxf_outputs(sys.argv[index + 1])
    else:
        main()
