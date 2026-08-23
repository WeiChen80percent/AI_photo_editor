# Third-party notice

This module is a modern PyTorch adaptation of the method and official source
code for:

Hui Zeng, Jianrui Cai, Lida Li, Zisheng Cao, and Lei Zhang, "Learning
Image-adaptive 3D Lookup Tables for High Performance Photo Enhancement in
Real-time," IEEE Transactions on Pattern Analysis and Machine Intelligence,
44(4), 2058-2073.

Official repository: https://github.com/HuiZeng/Image-Adaptive-3DLUT

The official repository is licensed under the Apache License, Version 2.0:
https://github.com/HuiZeng/Image-Adaptive-3DLUT/blob/master/LICENSE

Changes in this adaptation include manifest-based data loading, a mathematically
equivalent `torch.nn.functional.grid_sample` trilinear interpolator for current
PyTorch, leakage-safe validation, resumable checkpoints, Python evaluation
metrics, and command-line inference. The network architecture, initialization,
paired loss, regularization weights, augmentation ranges and default training
hyperparameters follow the paper and official paired-training code.

