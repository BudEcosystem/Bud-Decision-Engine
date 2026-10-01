"""Checks that the studio can use the GPU and import every model library.  python -m basal.doctor"""
import importlib
import sys

OK, BAD = "  ok ", "  !! "


def check_torch() -> tuple[int, list[str]]:
    """PyTorch and the GPU -> (problems, lines to print). Each failure is named for what it is: PyTorch missing, no
    GPU, the GPU out of memory right now, or the GPU test failing for another reason."""
    try:
        import torch
    except Exception as e:      # noqa: BLE001 - a broken install can raise more than ImportError
        return 1, [f"{BAD}PyTorch failed to import: {e}"]
    try:
        cuda = torch.cuda.is_available()
    except Exception as e:      # noqa: BLE001
        return 1, [f"{BAD}PyTorch {torch.__version__} could not look for a GPU: {e}"]
    lines = [f"{OK if cuda else BAD}PyTorch {torch.__version__} (CUDA {torch.version.cuda})"]
    if not cuda:
        return 1, lines + [f"{BAD}No CUDA GPU visible to PyTorch; models would run on the CPU"]
    name = "the GPU"
    try:
        name = torch.cuda.get_device_name(0)
        cc = ".".join(map(str, torch.cuda.get_device_capability(0)))
        x = torch.randn(512, 512, device="cuda", dtype=torch.bfloat16)
        (x @ x).sum().item()
    except Exception as e:      # noqa: BLE001
        if "out of memory" in str(e).lower() or type(e).__name__ == "OutOfMemoryError":
            return 1, lines + [f"{BAD}GPU: {name} is out of memory right now, so its test could not run. PyTorch itself "
                               "is fine. Close other programs that use the GPU (or eject models in the studio) and run "
                               "this check again; until then models load on the CPU."]
        return 1, lines + [f"{BAD}GPU: {name} is visible, but a test calculation on it failed: {e}"]
    return 0, lines + [f"{OK}GPU: {name}, compute capability {cc}, bfloat16 matmul works"]


def main() -> int:
    print("Bud Decision Studio environment check\n")
    problems, lines = check_torch()
    print("\n".join(lines))
    for mod, who in [("transformers", "all Hugging Face models"), ("peft", "Kev, Lev (LoRA adapters)"),
                     ("laya", "Laya family"), ("gliner2", "GLiNER2.5 Decide"), ("lev", "Lev"), ("kev", "Kev family"),
                     ("clm", "CLM 8B"), ("cv2", "Jev-Omni video"), ("librosa", "Jev-Omni audio"), ("fastapi", "the server")]:
        try:
            m = importlib.import_module(mod)
            print(f"{OK}{mod} {getattr(m, '__version__', '')}  ({who})")
        except Exception as e:
            problems += 1
            print(f"{BAD}{mod} missing: {e}  ({who})")
    try:
        importlib.import_module("fla")
        print(f"{OK}flash-linear-attention (fast Qwen3.5 kernels for Kev 4B, Lev, Intern-Decision)")
    except Exception:
        print("  --  flash-linear-attention not installed: Qwen3.5 models use slower reference kernels (optional)")
    print("\nAll good. Start the studio with ./run.sh" if not problems else f"\n{problems} problem(s) found.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
