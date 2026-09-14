# Research basis and provenance

This directory is a new PyTorch implementation. It does not import source code
or weights from `expert_retouch/vendor/deeplpf` or any of the projects below.
The architecture combines general research ideas and must be described with
citations; it must not be presented as unrelated to prior work.

Research basis:

1. Moran et al., **DeepLPF: Deep Local Parametric Filters for Image
   Enhancement**, CVPR 2020. Motivates interpretable, spatially local
   parametric editing instead of unconstrained pixel synthesis.
   <https://openaccess.thecvf.com/content_CVPR_2020/html/Moran_DeepLPF_Deep_Local_Parametric_Filters_for_Image_Enhancement_CVPR_2020_paper.html>
2. Moran et al., **CURL: Neural Curve Layers for Global Image Enhancement**,
   ICPR 2020. Motivates differentiable global colour curves.
   <https://arxiv.org/abs/1911.13175>
3. Gharbi et al., **Deep Bilateral Learning for Real-Time Image Enhancement**,
   SIGGRAPH 2017. Motivates predicting low-resolution transform parameters and
   applying them efficiently to a high-resolution image.
   <https://groups.csail.mit.edu/graphics/hdrnet/>
4. Zeng et al., **Learning Image-adaptive 3D Lookup Tables for High Performance
   Photo Enhancement in Real-time**, TPAMI 2020. Motivates image-adaptive,
   identity-initialized global colour transformations.
   <https://arxiv.org/abs/2009.14468>
5. Wang et al., **Real-Time Image Enhancer via Learnable Spatial-Aware 3D
   Lookup Tables**, ICCV 2021. Motivates combining global scene adaptation and
   spatial parameter maps.
   <https://openaccess.thecvf.com/content/ICCV2021/html/Wang_Real-Time_Image_Enhancer_via_Learnable_Spatial-Aware_3D_Lookup_Tables_ICCV_2021_paper.html>

Project-specific combination:

- one shared lightweight encoder;
- monotone piecewise-linear RGB curves;
- a bounded residual 3x3 colour matrix and bias;
- smooth local exposure, contrast and saturation maps;
- exact near-identity initialization for stable training from scratch;
- a paired sRGB/CIELab/SSIM/edge objective;
- explicit component switches for controlled ablation studies.

These points are an engineering/research contribution only after experiments
show what each component changes. Claims of novelty should be limited to the
specific combination and verified empirical findings.
