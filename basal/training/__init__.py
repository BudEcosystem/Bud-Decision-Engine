"""Fine-tuning for Bud Decision Studio: one PyTorch training engine, one standard data format, a small plugin per model
family. See docs/trainer/ARCHITECTURE.md.

The server imports only the light modules here (dataformat, capability, manager, api); PyTorch and the model libraries
are imported inside the training job process (python -m basal.training.job), never in the server.
"""
