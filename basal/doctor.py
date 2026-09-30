"""Checks that the studio can use the GPU and import every model library.  python -m basal.doctor"""
import importlib
import sys

OK, BAD = "  ok ", "  !! "


def main() -> int:
    problems = 0
    print("Bud Decision Studio environment check\n")
    try:
        import torch
        cuda = torch.cuda.is_available()
        print(f"{OK if cuda else BAD}PyTorch {torch.__version__} (CUDA {torch.version.cuda})")
        if cuda:
            name = torch.cuda.get_device_name(0)
            cc = ".".join(map(str, torch.cuda.get_device_capability(0)))
            x = torch.randn(512, 512, device="cuda", dtype=torch.bfloat16)
            (x @ x).sum().item()
            print(f"{OK}GPU: {name}, compute capability {cc}, bfloat16 matmul works")
        else:
            problems += 1
            print(f"{BAD}No CUDA GPU visible to PyTorch; models would run on the CPU")
    except Exception as e:
        problems += 1
        print(f"{BAD}PyTorch failed to import: {e}")
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
