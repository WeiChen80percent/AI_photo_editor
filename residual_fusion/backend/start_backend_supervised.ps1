param(
    [switch]$CheckOnly,
    [switch]$UseExpertCV31,
    [switch]$UseExpertCV35,
    [switch]$UseExpertCV38
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutMs = 300
    )

    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        if (-not $connection.Wait($TimeoutMs)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

Set-Location -LiteralPath $PSScriptRoot

$env:AI_PHOTO_USE_SUPERVISED_PIPELINE = "1"
$env:AI_PHOTO_USE_LLM = "1"
$env:AI_PHOTO_PROMPT_MODEL = "ai-photo-prompt-control:exp007-v1"
$env:AI_PHOTO_PROMPT_TIMEOUT = "30"
$env:AI_PHOTO_PROMPT_USE_GUARD = "0"
$env:AI_PHOTO_BANDING_GUARD = "1"
$env:AI_PHOTO_LOCAL_SAFETY_GUARD = "1"
$useExpertCV3Base = $UseExpertCV31 -or $UseExpertCV35 -or $UseExpertCV38
$env:AI_PHOTO_USE_EXPERT_C_V3_1 = if ($useExpertCV3Base) { "1" } else { "0" }
$env:AI_PHOTO_USE_EXPERT_C_V3_5 = if ($UseExpertCV35) { "1" } else { "0" }
$env:AI_PHOTO_USE_EXPERT_C_V3_8 = if ($UseExpertCV38) { "1" } else { "0" }
$env:AI_PHOTO_V3_8_STRICT_NOOP = "0"
$env:AI_PHOTO_V3_1_MAX_SIDE = "2560"
$env:AI_PHOTO_PRELOAD_SEGMENTATION = "1"
$env:AI_PHOTO_SEGMENTATION_DEVICE = "cuda"
$env:AI_PHOTO_SEGMENTATION_HALF = "1"
$env:AI_PHOTO_SEGMENTATION_LOCAL_ONLY = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

if (-not $CheckOnly -and (Test-TcpPort -HostName "127.0.0.1" -Port 8000)) {
    throw "Port 8000 is already in use. Stop the existing backend or use it instead."
}

$modelName = $env:AI_PHOTO_PROMPT_MODEL
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is not installed or is not available on PATH."
}
$ollamaOutput = & ollama list 2>&1
$ollamaExitCode = $LASTEXITCODE
$ollamaOutputText = ($ollamaOutput | Out-String).Trim()
if ($ollamaExitCode -ne 0) {
    throw "Ollama service is unavailable. Start Ollama and retry. $ollamaOutputText"
}
if ($ollamaOutputText -notmatch [regex]::Escape($modelName)) {
    throw "Ollama model is missing: $modelName"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$artifacts = @(
    Join-Path $repoRoot "training\baselines\selective_hybrid_numpy_v1.json"
    Join-Path $repoRoot "training\outputs\selective_hybrid_numpy_v1\shared_v2_state_intent.npz"
    Join-Path $repoRoot "training\outputs\selective_hybrid_numpy_v1\white_balance_expert.npz"
)
if ($useExpertCV3Base) {
    $artifacts += @(
        Join-Path $repoRoot "training\v3\pre_training_freeze_joint_categorical_artifact_safe_full_research_v1.json"
        Join-Path $repoRoot "training\outputs\expert_c_v3_1_joint_categorical_artifact_safe_full_research001\joint_categorical_strength_full_train.pt"
        Join-Path $repoRoot "training\outputs\expert_c_v3_1_artifact_safe_development001\result.json"
    )
}
if ($UseExpertCV35) {
    $artifacts += @(
        Join-Path $repoRoot "training\outputs\expert_c_v3_5_resolution_aligned_selector002\result.json"
        Join-Path $repoRoot "training\outputs\expert_c_v3_5_resolution_aligned_selector002\resolution_aligned_selector_v3_5.joblib"
        Join-Path $repoRoot "training\outputs\expert_c_v3_5_development003\result.json"
    )
}
if ($UseExpertCV38) {
    $artifacts += @(
        Join-Path $repoRoot "training\outputs\expert_c_continuous_spatial_strength_post_final_5000_demo001\result.json"
        Join-Path $repoRoot "training\outputs\expert_c_continuous_spatial_strength_final002\result.json"
        Join-Path $repoRoot "training\outputs\expert_c_v3_8_post_final_5000_release_audit001\result.json"
    )
}
foreach ($artifact in $artifacts) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Supervised model artifact is missing: $artifact"
    }
}

$candidates = @(
    (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot "training\.venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent $repoRoot) ".venv\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
)
$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) {
        continue
    }
    & $candidate -c "import cv2, fastapi, numpy, torch, transformers, uvicorn; assert torch.cuda.is_available()" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No CUDA Python environment with FastAPI, OpenCV, PyTorch, Transformers, NumPy, and Uvicorn was found."
}

& $python -c "import os, numpy as np; from app.main import app; from app.services.expert_c_v3_contract import expert_c_v3_enabled, expert_c_v3_5_enabled, expert_c_v3_8_enabled; from app.services.selective_hybrid_predictor import predict_selective_hybrid_parameters; from app.services.supervised_edit_pipeline import should_use_supervised_prompt_edit; from app.services.supervised_opencv_processor import supervised_banding_guard_enabled; from app.services.local_edit_safety import local_edit_safety_guard_enabled; image=np.full((24,24,3),128,dtype=np.uint8); [predict_selective_hybrid_parameters(image,intent) for intent in ('auto_enhance','fix_exposure','fix_white_balance','restore_natural')]; assert expert_c_v3_enabled() == any(os.getenv(name) == '1' for name in ('AI_PHOTO_USE_EXPERT_C_V3_1','AI_PHOTO_USE_EXPERT_C_V3_5','AI_PHOTO_USE_EXPERT_C_V3_8')); assert expert_c_v3_5_enabled() == (os.getenv('AI_PHOTO_USE_EXPERT_C_V3_5') == '1'); assert expert_c_v3_8_enabled() == (os.getenv('AI_PHOTO_USE_EXPERT_C_V3_8') == '1'); assert should_use_supervised_prompt_edit('make this photo look better',engine_name='opencv',has_parent_edit=False,semantic_disposition='legacy_fallback'); assert supervised_banding_guard_enabled(); assert local_edit_safety_guard_enabled(); assert app.title=='AI Photo Editor Backend'"
if ($LASTEXITCODE -ne 0) {
    throw "Supervised backend integration self-check failed."
}

Write-Host "Starting supervised AI Photo Editor backend..."
Write-Host "Python=$python"
Write-Host "Prompt model=$modelName"
if ($UseExpertCV38) {
    Write-Host "Pipeline=Qwen3 BF16 LoRA -> semantic residual editor -> risk-aware region fusion -> OpenCV+PyTorch renderer"
}
elseif ($UseExpertCV35) {
    Write-Host "Pipeline=Qwen3 BF16 LoRA -> risk-aware selector -> semantic residual editor / identity -> OpenCV+PyTorch renderer"
}
elseif ($UseExpertCV31) {
    Write-Host "Pipeline=Qwen3 BF16 LoRA -> semantic residual editor / global parametric tools -> OpenCV+PyTorch renderer"
}
else {
    Write-Host "Pipeline=Qwen3 BF16 LoRA -> NumPy regressor -> Expert C OpenCV renderer"
}
Write-Host "Semantic residual editor=$env:AI_PHOTO_USE_EXPERT_C_V3_1, max_side=$env:AI_PHOTO_V3_1_MAX_SIDE"
Write-Host "Risk-aware selector=$env:AI_PHOTO_USE_EXPERT_C_V3_5"
Write-Host "Region-aware fusion=$env:AI_PHOTO_USE_EXPERT_C_V3_8"
Write-Host "Safety fallback=visible conservative spatial blend"
Write-Host "Team tools=style catalog + semantic masks + adaptive/manual refinement"
Write-Host "Prompt guard=$env:AI_PHOTO_PROMPT_USE_GUARD"
Write-Host "Banding guard=$env:AI_PHOTO_BANDING_GUARD"
Write-Host "Local safety guard=$env:AI_PHOTO_LOCAL_SAFETY_GUARD"
Write-Host "Segmentation=$env:AI_PHOTO_SEGMENTATION_DEVICE FP16, local cache only"
Write-Host "Swagger=http://127.0.0.1:8000/docs"

if ($CheckOnly) {
    if ($UseExpertCV38) {
        $v38Info = & $python -c "import json; from app.services.expert_c_v3_8_runtime import warmup_expert_c_v3_8; print(json.dumps(warmup_expert_c_v3_8()))"
        if ($LASTEXITCODE -ne 0) {
            throw "Region-aware fusion CUDA warmup failed."
        }
        Write-Host "Region-aware fusion warmup=$v38Info"
    }
    elseif ($UseExpertCV35) {
        $v35Info = & $python -c "import json; from app.services.expert_c_v3_5_runtime import warmup_expert_c_v3_5; print(json.dumps(warmup_expert_c_v3_5()))"
        if ($LASTEXITCODE -ne 0) {
            throw "Risk-aware selector CUDA warmup failed."
        }
        Write-Host "Risk-aware selector warmup=$v35Info"
    }
    elseif ($UseExpertCV31) {
        $v31Info = & $python -c "import json; from app.services.expert_c_v3_runtime import warmup_expert_c_v3; print(json.dumps(warmup_expert_c_v3()))"
        if ($LASTEXITCODE -ne 0) {
            throw "Semantic residual editor CUDA warmup failed."
        }
        Write-Host "Semantic residual editor warmup=$v31Info"
    }
    if ($UseExpertCV38) {
        Write-Host "Segmentation warmup=covered by region-aware fusion warmup"
    }
    else {
        $segmentationInfo = & $python -c "import json; from app.services.semantic_mask_service import get_default_semantic_mask_service; print(json.dumps(get_default_semantic_mask_service().warmup()))"
        if ($LASTEXITCODE -ne 0) {
            throw "Semantic segmentation CUDA warmup failed."
        }
        Write-Host "Segmentation warmup=$segmentationInfo"
    }
    Write-Host "status=PASS"
    exit 0
}

if ($UseExpertCV38) {
    Write-Host "Region-aware fusion will warm up inside the persistent Uvicorn lifespan."
}
elseif ($UseExpertCV35) {
    Write-Host "Prewarming risk-aware selector and semantic residual editor..."
    $v35Info = & $python -c "import json; from app.services.expert_c_v3_5_runtime import warmup_expert_c_v3_5; print(json.dumps(warmup_expert_c_v3_5()))"
    if ($LASTEXITCODE -ne 0) {
        throw "Risk-aware selector CUDA warmup failed."
    }
    Write-Host "Risk-aware selector warmup=$v35Info"
}
elseif ($UseExpertCV31) {
    Write-Host "Prewarming semantic residual editor..."
    $v31Info = & $python -c "import json; from app.services.expert_c_v3_runtime import warmup_expert_c_v3; print(json.dumps(warmup_expert_c_v3()))"
    if ($LASTEXITCODE -ne 0) {
        throw "Semantic residual editor CUDA warmup failed."
    }
    Write-Host "Semantic residual editor warmup=$v31Info"
}

Write-Host "Prewarming Ollama prompt model..."
$ollamaWarmupBody = @{
    model = $modelName
    prompt = ""
    stream = $false
    keep_alive = "15m"
} | ConvertTo-Json -Compress
$ollamaWarmupStarted = [Diagnostics.Stopwatch]::StartNew()
try {
    $null = Invoke-RestMethod `
        -Uri "http://127.0.0.1:11434/api/generate" `
        -Method Post `
        -ContentType "application/json" `
        -Body $ollamaWarmupBody `
        -TimeoutSec 60
}
catch {
    throw "Ollama prompt-model warmup failed: $($_.Exception.Message)"
}
finally {
    $ollamaWarmupStarted.Stop()
}
Write-Host (
    "Ollama warmup={0:N2}s, keep_alive=15m" -f `
        $ollamaWarmupStarted.Elapsed.TotalSeconds
)

& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
