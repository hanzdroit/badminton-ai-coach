"""Patch upstream TrackNetV3 inference for CPU-capable execution."""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
if "device = torch.device(" not in source:
    marker = "import torch"
    if marker not in source:
        raise SystemExit("Could not find torch import in upstream predict.py")
    source = source.replace(
        marker,
        marker + '\n\ndevice = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
        1,
    )
source = source.replace(".cuda()", ".to(device)")
source = source.replace("torch.load(args.tracknet_file)", "torch.load(args.tracknet_file, map_location=device)")
source = source.replace("torch.load(args.inpaintnet_file)", "torch.load(args.inpaintnet_file, map_location=device)")
if ".cuda()" in source:
    raise SystemExit("CPU patch incomplete: .cuda() remains in predict.py")
path.write_text(source, encoding="utf-8")
print(f"Patched {path} for CPU/CUDA device selection")
