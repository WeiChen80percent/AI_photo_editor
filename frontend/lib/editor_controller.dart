import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'api_service.dart';
import 'edit_models.dart';

enum EditorTool { prompt, styles, reference, manual, history }

enum ComparisonView { original, compare, result }

enum ComparisonBaseline { original, parent }

@immutable
class EditorPresentationMessage {
  const EditorPresentationMessage(
    this.code, {
    this.arguments = const <String, Object?>{},
    this.details = const <String, dynamic>{},
  });

  final String code;
  final Map<String, Object?> arguments;
  final Map<String, dynamic> details;
}

class EditorController extends ChangeNotifier {
  static const String originalParentSentinel = 'original';

  EditorController({
    required EditorApi api,
    this.previewDebounce = const Duration(milliseconds: 250),
  }) : _api = api;

  final EditorApi _api;
  final Duration previewDebounce;

  Uint8List? originalImageBytes;
  Uint8List? referenceImageBytes;
  String? originalImageUrl;
  String promptDraft = '';
  String? sessionId;
  String? selectedEditId;
  List<EditHistoryItem> history = <EditHistoryItem>[];
  EditHistoryItem? selectedEdit;
  EditorTool? activeTool;
  ComparisonView comparisonView = ComparisonView.result;
  ComparisonBaseline comparisonBaseline = ComparisonBaseline.original;
  double comparisonSplit = 0.5;

  bool isProcessing = false;
  String? errorMessage;
  String? statusMessage;
  EditorPresentationMessage? errorPresentation;
  EditorPresentationMessage? statusPresentation;

  ManualSchema? manualSchema;
  StyleCatalog? styleCatalog;
  String? selectedStyleFamily;
  bool isLoadingStyles = false;
  final Map<String, double> _styleStrengths = <String, double>{};
  String? manualSourceEditId;
  Map<String, double> manualSourceValues = <String, double>{};
  Map<String, double> manualValues = <String, double>{};
  ManualEditResponse? manualPreview;
  bool isLoadingManual = false;
  bool isPreviewing = false;
  bool isCommittingManual = false;
  bool manualAdvancedExpanded = false;

  PhotoGitOperation photoGitOperation = PhotoGitOperation.merge;
  String photoGitInstruction = '';
  String? photoGitSourceEditId;
  String? photoGitRevertEditId;
  String? photoGitRegion;
  String? photoGitParameter;
  Map<String, String> photoGitResolutions = <String, String>{};
  PhotoGitPlan? photoGitPlan;
  PhotoGitPreview? photoGitPreview;
  bool isPlanningPhotoGit = false;
  bool isPreviewingPhotoGit = false;
  bool isCommittingPhotoGit = false;

  Timer? _previewTimer;
  http.Client? _previewClient;
  int _previewSequence = 0;
  int _photoGitRequestSequence = 0;
  String? _photoGitClientRequestId;
  bool _disposed = false;

  bool get hasOriginal =>
      originalImageBytes != null || originalImageUrl != null;

  bool get hasResult => currentResultUrl != null;

  String? get currentResultUrl =>
      photoGitPreview?.resultUrl ??
      manualPreview?.resultUrl ??
      selectedEdit?.resultUrl;

  EditHistoryItem? get comparisonParentEdit {
    if (photoGitPreview != null) {
      return _findEdit(photoGitPreview!.targetEditId);
    }
    final parentEditId = manualPreview != null && manualIsDirty
        ? manualSourceEditId
        : selectedEdit?.parentEditId;
    if (parentEditId == null || parentEditId == originalParentSentinel) {
      return null;
    }
    return _findEdit(parentEditId);
  }

  bool get canCompareWithParent => comparisonParentEdit != null;

  bool get comparisonUsesParent =>
      comparisonBaseline == ComparisonBaseline.parent && canCompareWithParent;

  Uint8List? get comparisonBaselineBytes =>
      comparisonUsesParent ? null : originalImageBytes;

  String? get comparisonBaselineUrl =>
      comparisonUsesParent ? comparisonParentEdit!.resultUrl : originalImageUrl;

  bool get isOriginalBaseSelected =>
      selectedEdit == null && selectedEditId == originalParentSentinel;

  Map<String, dynamic> get currentParameters =>
      manualPreview?.parameters ?? selectedEdit?.parameters ?? const {};

  ParameterMetadataCatalog metadataCatalogFor(EditHistoryItem? edit) {
    if (edit == null) {
      return ParameterMetadataCatalog.fromSources(manualSchema: manualSchema);
    }
    return edit.parameterMetadataCatalog(manualSchema: manualSchema);
  }

  ParameterMetadataCatalog get parameterMetadataCatalog =>
      metadataCatalogFor(selectedEdit);

  bool get hasUncommittedPreview =>
      photoGitPreview != null || (manualPreview != null && manualIsDirty);

  bool get hasPhotoGitDraft =>
      photoGitSourceEditId != null ||
      photoGitRevertEditId != null ||
      photoGitInstruction.trim().isNotEmpty ||
      photoGitRegion != null ||
      photoGitParameter != null ||
      photoGitResolutions.isNotEmpty ||
      photoGitPlan != null ||
      photoGitPreview != null;

  bool get hasPendingDraft => manualIsDirty || hasPhotoGitDraft;

  EditHistoryItem? get photoGitTargetEdit => selectedEdit;

  List<EditHistoryItem> get photoGitSourceCandidates {
    final targetId = selectedEditId;
    return history
        .where(
          (edit) =>
              edit.editId != targetId && _isPhotoGitCapableMode(edit.editMode),
        )
        .toList(growable: false);
  }

  List<EditHistoryItem> get photoGitRevertCandidates {
    final target = selectedEdit;
    if (target == null) {
      return const <EditHistoryItem>[];
    }
    final result = <EditHistoryItem>[];
    final visited = <String>{};
    EditHistoryItem? current = target;
    while (current != null && visited.add(current.editId)) {
      if (current.editMode == 'prompt' || current.editMode == 'manual') {
        result.add(current);
      }
      final parentId = current.parentEditId;
      if (parentId == null || parentId == originalParentSentinel) {
        break;
      }
      current = _findEdit(parentId);
    }
    return result;
  }

  List<PhotoGitSelector> get photoGitSelectors {
    if (photoGitRegion == null && photoGitParameter == null) {
      return const <PhotoGitSelector>[];
    }
    return <PhotoGitSelector>[
      PhotoGitSelector(
        region: photoGitRegion,
        parameters: photoGitParameter == null
            ? const <String>[]
            : <String>[photoGitParameter!],
      ),
    ];
  }

  bool get canPlanPhotoGit {
    if (isPlanningPhotoGit ||
        isPreviewingPhotoGit ||
        isCommittingPhotoGit ||
        manualIsDirty ||
        sessionId == null ||
        selectedEdit == null) {
      return false;
    }
    final hasScope =
        photoGitInstruction.trim().isNotEmpty || photoGitSelectors.isNotEmpty;
    if (!hasScope) {
      return false;
    }
    return photoGitOperation == PhotoGitOperation.merge
        ? photoGitSourceEditId != null
        : photoGitRevertEditId != null;
  }

  bool get canPreviewPhotoGit =>
      !isPlanningPhotoGit &&
      !isPreviewingPhotoGit &&
      !isCommittingPhotoGit &&
      photoGitPlan?.isReady == true &&
      photoGitPlan?.hasUnresolvedConflicts == false;

  bool get canCommitPhotoGit =>
      !isPlanningPhotoGit &&
      !isPreviewingPhotoGit &&
      !isCommittingPhotoGit &&
      photoGitPreview != null &&
      photoGitPreview!.planHash == photoGitPlan?.planHash &&
      photoGitPlan?.isReady == true;

  bool get isRenderingPreview => isPreviewing || isPreviewingPhotoGit;

  bool get manualIsDirty {
    if (manualSourceEditId == null || manualValues.isEmpty) {
      return false;
    }
    for (final entry in manualValues.entries) {
      final source = manualSourceValues[entry.key];
      if (source == null || (source - entry.value).abs() > 0.000001) {
        return true;
      }
    }
    return false;
  }

  bool get canSubmitPrompt =>
      !isProcessing &&
      !hasPhotoGitDraft &&
      promptDraft.trim().isNotEmpty &&
      (hasOriginal || selectedEdit != null);

  bool get canSubmitReference =>
      !isProcessing &&
      !hasPhotoGitDraft &&
      referenceImageBytes != null &&
      (hasOriginal || selectedEdit != null);

  List<StyleCatalogItem> get visibleStyles {
    final styles = styleCatalog?.styles ?? const <StyleCatalogItem>[];
    final family = selectedStyleFamily;
    if (family == null) {
      return styles;
    }
    return styles
        .where((style) => style.family == family)
        .toList(growable: false);
  }

  double styleStrengthFor(StyleCatalogItem style) =>
      _styleStrengths[style.styleId] ?? style.defaultStrength;

  void setStyleFamily(String? family) {
    selectedStyleFamily = family;
    _notify();
  }

  void setStyleStrength(StyleCatalogItem style, double value) {
    _styleStrengths[style.styleId] = value
        .clamp(style.minimumStrength, style.maximumStrength)
        .toDouble();
    _notify();
  }

  bool get canOpenManual {
    final edit = selectedEdit;
    return !hasPhotoGitDraft &&
        edit != null &&
        edit.engine.toLowerCase() == 'opencv' &&
        (edit.editMode == 'prompt' ||
            edit.editMode == 'manual' ||
            edit.editMode == 'photo_git_merge' ||
            edit.editMode == 'photo_git_revert');
  }

  String get manualDisabledReason {
    if (hasPhotoGitDraft) {
      return '請先完成或取消目前的版本操作，再進入手動調整。';
    }
    final edit = selectedEdit;
    if (edit == null) {
      return '請先完成一次指令修圖，再進入手動調整。';
    }
    if (edit.editMode == 'reference') {
      return '參考圖結果目前不能進入手動調整，請先選擇指令或手動版本。';
    }
    if (edit.engine.toLowerCase() != 'opencv') {
      return '手動調整第一版只支援 OpenCV 結果。';
    }
    return '這個版本目前不支援手動調整。';
  }

  String get manualDisabledCode {
    if (hasPhotoGitDraft) {
      return 'photo_git_draft_active';
    }
    final edit = selectedEdit;
    if (edit == null) {
      return 'manual_need_prompt';
    }
    if (edit.editMode == 'reference') {
      return 'manual_reference_unsupported';
    }
    if (edit.engine.toLowerCase() != 'opencv') {
      return 'manual_engine_unsupported';
    }
    return 'manual_unavailable';
  }

  String get currentSummary {
    final edit = selectedEdit;
    if (edit == null) {
      if (isOriginalBaseSelected) {
        return '原圖 · 下一次修圖會建立新分支';
      }
      return '選擇圖片後，使用指令或參考圖開始修圖';
    }
    final target = regionLabel(
      (currentParameters['region'] ?? edit.region).toString(),
    );
    final displayParameters = hasUncommittedPreview
        ? currentParameters
        : edit.parametersForDisplay(parameterMetadataCatalog);
    final parameters = compactParameterSummary(
      displayParameters,
      metadataCatalog: parameterMetadataCatalog,
    );
    final previewLabel = hasUncommittedPreview ? '預覽 · ' : '';
    final styleLabel = edit.style == null
        ? ''
        : '${edit.style!.displayName} ${(edit.style!.strength * 100).round()}% · ';
    if (parameters.isEmpty) {
      return '$previewLabel$styleLabel$target · ${edit.modeLabel}';
    }
    final parameterLabel = edit.isDirectStyleEdit && !hasUncommittedPreview
        ? '等效 $parameters'
        : parameters;
    return '$previewLabel$styleLabel$target · $parameterLabel';
  }

  void setPhotoGitOperation(PhotoGitOperation operation) {
    if (photoGitOperation == operation) {
      return;
    }
    photoGitOperation = operation;
    photoGitSourceEditId = null;
    photoGitRevertEditId = null;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  void setPhotoGitSource(String? editId) {
    if (photoGitSourceEditId == editId) {
      return;
    }
    photoGitSourceEditId = editId;
    photoGitRevertEditId = null;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  void setPhotoGitRevertStep(String? editId) {
    if (photoGitRevertEditId == editId) {
      return;
    }
    photoGitRevertEditId = editId;
    photoGitSourceEditId = null;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  void setPhotoGitInstruction(String value) {
    if (photoGitInstruction == value) {
      return;
    }
    photoGitInstruction = value;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  void setPhotoGitRegion(String? region) {
    if (photoGitRegion == region) {
      return;
    }
    photoGitRegion = region;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  void setPhotoGitParameter(String? parameter) {
    if (photoGitParameter == parameter) {
      return;
    }
    photoGitParameter = parameter;
    _invalidatePhotoGitPlan();
    _clearError();
    _clearStatus();
    _notify();
  }

  Future<bool> analyzePhotoGit() async {
    final currentSession = sessionId;
    final request = _buildPhotoGitRequest();
    if (currentSession == null || request == null || !canPlanPhotoGit) {
      _setError('請選擇版本並指定要操作的區域或參數。', 'photo_git_request_incomplete');
      _notify();
      return false;
    }
    isPlanningPhotoGit = true;
    photoGitPreview = null;
    _photoGitClientRequestId = null;
    _clearError();
    _setStatus('正在分析版本差異…', 'photo_git_planning');
    _notify();
    try {
      final plan = await _api.planPhotoGit(
        sessionId: currentSession,
        request: request,
      );
      photoGitPlan = plan;
      photoGitResolutions = <String, String>{
        for (final conflict in plan.conflicts)
          if (conflict.resolvedChoice != null)
            conflict.conflictId: conflict.resolvedChoice!,
      };
      if (plan.status == 'conflict') {
        _setStatus(plan.message, 'photo_git_conflicts_found');
      } else if (plan.status == 'no_change') {
        _setStatus(plan.message, 'photo_git_no_change');
      } else {
        _setStatus(plan.message, 'photo_git_plan_ready');
      }
      return plan.isReady;
    } on ApiException catch (error) {
      if (error.code == 'photo_git_plan_stale') {
        photoGitPlan = null;
        photoGitPreview = null;
        _photoGitClientRequestId = null;
      }
      _setApiError(error);
      _clearStatus();
      return false;
    } catch (error) {
      _setError(
        '版本分析失敗：$error',
        'photo_git_plan_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      _clearStatus();
      return false;
    } finally {
      isPlanningPhotoGit = false;
      _notify();
    }
  }

  Future<bool> resolvePhotoGitConflict(String conflictId, String choice) async {
    PhotoGitConflict? conflict;
    for (final item in photoGitPlan?.conflicts ?? const <PhotoGitConflict>[]) {
      if (item.conflictId == conflictId) {
        conflict = item;
        break;
      }
    }
    if (conflict == null || !conflict.allowedChoices.contains(choice)) {
      return false;
    }
    photoGitResolutions = <String, String>{
      ...photoGitResolutions,
      conflictId: choice,
    };
    photoGitPlan = null;
    photoGitPreview = null;
    _photoGitClientRequestId = null;
    _notify();
    return analyzePhotoGit();
  }

  Future<bool> previewPhotoGit() async {
    final currentSession = sessionId;
    final request = _buildPhotoGitRequest();
    final plan = photoGitPlan;
    if (currentSession == null ||
        request == null ||
        plan == null ||
        !canPreviewPhotoGit) {
      return false;
    }
    isPreviewingPhotoGit = true;
    _clearError();
    _setStatus('正在產生版本預覽…', 'photo_git_previewing');
    _notify();
    try {
      final preview = await _api.previewPhotoGit(
        sessionId: currentSession,
        request: request,
        planHash: plan.planHash,
      );
      if (photoGitPlan?.planHash != preview.planHash) {
        return false;
      }
      photoGitPreview = preview;
      comparisonView = ComparisonView.compare;
      comparisonBaseline = ComparisonBaseline.parent;
      _ensureComparisonState();
      _setStatus('預覽已就緒，請比較後再建立版本。', 'photo_git_preview_ready');
      return true;
    } on ApiException catch (error) {
      if (error.code == 'photo_git_plan_stale') {
        photoGitPlan = null;
        photoGitPreview = null;
        _photoGitClientRequestId = null;
      }
      _setApiError(error);
      _clearStatus();
      return false;
    } catch (error) {
      _setError(
        '版本預覽失敗：$error',
        'photo_git_preview_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      _clearStatus();
      return false;
    } finally {
      isPreviewingPhotoGit = false;
      _notify();
    }
  }

  Future<bool> commitPhotoGit() async {
    final currentSession = sessionId;
    final request = _buildPhotoGitRequest();
    final plan = photoGitPlan;
    if (currentSession == null ||
        request == null ||
        plan == null ||
        !canCommitPhotoGit) {
      return false;
    }
    isCommittingPhotoGit = true;
    _photoGitClientRequestId ??=
        'flutter_photo_git_${DateTime.now().microsecondsSinceEpoch}_'
        '${++_photoGitRequestSequence}';
    final requestId = _photoGitClientRequestId!;
    _clearError();
    _setStatus('正在建立可追蹤版本…', 'photo_git_committing');
    _notify();
    try {
      final item = await _api.commitPhotoGit(
        sessionId: currentSession,
        request: request,
        planHash: plan.planHash,
        clientRequestId: requestId,
      );
      _applyCommittedItem(item);
      await refreshHistory(preferredEditId: item.editId, quiet: true);
      _clearPhotoGitDraft(notify: false);
      _setStatus('版本已建立並加入歷史紀錄。', 'photo_git_committed');
      comparisonView = ComparisonView.result;
      return true;
    } on ApiException catch (error) {
      if (error.code == 'photo_git_plan_stale') {
        photoGitPlan = null;
        photoGitPreview = null;
        _photoGitClientRequestId = null;
      }
      _setApiError(error);
      _clearStatus();
      return false;
    } catch (error) {
      _setError(
        '建立版本失敗：$error',
        'photo_git_commit_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      _clearStatus();
      return false;
    } finally {
      isCommittingPhotoGit = false;
      _notify();
    }
  }

  void discardPhotoGitDraft() {
    _clearPhotoGitDraft(notify: false);
    _clearError();
    _clearStatus();
    _ensureComparisonState();
    _notify();
  }

  void setActiveTool(EditorTool? tool) {
    activeTool = tool;
    clearMessages();
    _notify();
  }

  void setComparisonView(ComparisonView view) {
    final nextView = view == ComparisonView.original || hasResult
        ? view
        : ComparisonView.original;
    if (comparisonView == nextView) {
      return;
    }
    comparisonView = nextView;
    _notify();
  }

  void setComparisonBaseline(ComparisonBaseline baseline) {
    final nextBaseline =
        baseline == ComparisonBaseline.parent && !canCompareWithParent
        ? ComparisonBaseline.original
        : baseline;
    if (comparisonBaseline == nextBaseline) {
      return;
    }
    comparisonBaseline = nextBaseline;
    _notify();
  }

  void setComparisonSplit(double value) {
    final nextValue = value.clamp(0.05, 0.95).toDouble();
    if ((comparisonSplit - nextValue).abs() < 0.000001) {
      return;
    }
    comparisonSplit = nextValue;
    _notify();
  }

  void setPromptDraft(String value) {
    promptDraft = value;
    _clearError();
    _clearStatus();
    _notify();
  }

  void setOriginalImage(Uint8List bytes) {
    _cancelPreview(clearDraft: true);
    _clearPhotoGitDraft(notify: false);
    originalImageBytes = bytes;
    originalImageUrl = null;
    referenceImageBytes = null;
    sessionId = null;
    selectedEditId = null;
    selectedEdit = null;
    history = <EditHistoryItem>[];
    comparisonView = ComparisonView.original;
    comparisonBaseline = ComparisonBaseline.original;
    comparisonSplit = 0.5;
    _clearError();
    _setStatus('已選擇新的原始圖片', 'status_selected_new_original');
    _notify();
  }

  void clearOriginalImage() {
    _cancelPreview(clearDraft: true);
    _clearPhotoGitDraft(notify: false);
    originalImageBytes = null;
    originalImageUrl = null;
    referenceImageBytes = null;
    sessionId = null;
    selectedEditId = null;
    selectedEdit = null;
    history = <EditHistoryItem>[];
    comparisonView = ComparisonView.original;
    comparisonBaseline = ComparisonBaseline.original;
    comparisonSplit = 0.5;
    _clearError();
    _clearStatus();
    _notify();
  }

  void setReferenceImage(Uint8List bytes) {
    referenceImageBytes = bytes;
    _clearError();
    _setStatus('參考圖已準備完成', 'status_reference_ready');
    _notify();
  }

  void clearReferenceImage() {
    referenceImageBytes = null;
    _clearError();
    _notify();
  }

  void clearMessages() {
    _clearError();
    _clearStatus();
  }

  void _setError(
    String legacyText,
    String code, {
    Map<String, Object?> arguments = const <String, Object?>{},
    Map<String, dynamic> details = const <String, dynamic>{},
  }) {
    errorMessage = legacyText;
    errorPresentation = EditorPresentationMessage(
      code,
      arguments: arguments,
      details: details,
    );
  }

  void _setStatus(
    String legacyText,
    String code, {
    Map<String, Object?> arguments = const <String, Object?>{},
    Map<String, dynamic> details = const <String, dynamic>{},
  }) {
    statusMessage = legacyText;
    statusPresentation = EditorPresentationMessage(
      code,
      arguments: arguments,
      details: details,
    );
  }

  void _setApiError(ApiException error) {
    _setError(
      _friendlyApiMessage(error),
      error.code ?? 'backend_error',
      arguments: <String, Object?>{'statusCode': error.statusCode},
      details: error.details,
    );
  }

  void _clearError() {
    errorMessage = null;
    errorPresentation = null;
  }

  void _clearStatus() {
    statusMessage = null;
    statusPresentation = null;
  }

  Future<bool> submitPrompt() async {
    final prompt = promptDraft.trim();
    if (prompt.isEmpty) {
      _setError('請輸入修圖指令。', 'prompt_required');
      _notify();
      return false;
    }
    return _submitEdit(prompt: prompt, referenceBytes: null);
  }

  Future<bool> submitReference() async {
    final reference = referenceImageBytes;
    if (reference == null) {
      _setError('請先選擇參考圖片。', 'reference_required');
      _notify();
      return false;
    }
    return _submitEdit(prompt: '', referenceBytes: reference);
  }

  Future<bool> openStyles() async {
    activeTool = EditorTool.styles;
    if (styleCatalog != null) {
      _notify();
      return true;
    }
    isLoadingStyles = true;
    _clearError();
    _notify();
    try {
      styleCatalog = await _api.fetchStyleCatalog();
      for (final style in styleCatalog!.styles) {
        _styleStrengths.putIfAbsent(style.styleId, () => style.defaultStrength);
      }
      return true;
    } on ApiException catch (error) {
      _setApiError(error);
      return false;
    } catch (error) {
      _setError(
        '無法載入風格目錄：$error',
        'style_catalog_load_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      return false;
    } finally {
      isLoadingStyles = false;
      _notify();
    }
  }

  Future<bool> applyStyle(StyleCatalogItem style) {
    final strength = styleStrengthFor(style);
    return _submitEdit(
      prompt: '${style.styleId} 強度 ${strength.toStringAsFixed(2)}',
      referenceBytes: null,
    );
  }

  Future<bool> _submitEdit({
    required String prompt,
    required Uint8List? referenceBytes,
  }) async {
    if (isProcessing) {
      return false;
    }
    if (hasPhotoGitDraft) {
      _setError('請先完成或取消目前的版本操作。', 'photo_git_draft_active');
      _notify();
      return false;
    }
    final canContinue = sessionId != null && selectedEditId != null;
    if (!canContinue && originalImageBytes == null) {
      _setError('請先選擇原始圖片。', 'original_required');
      _notify();
      return false;
    }

    isProcessing = true;
    _clearError();
    if (referenceBytes == null) {
      _setStatus('正在解析修圖指令…', 'parsing_prompt');
    } else {
      _setStatus('正在套用參考圖…', 'applying_reference');
    }
    _notify();

    try {
      final item = await _api.submitEdit(
        originalBytes: canContinue ? null : originalImageBytes,
        referenceBytes: referenceBytes,
        prompt: prompt,
        sessionId: canContinue ? sessionId : null,
        parentEditId: canContinue ? selectedEditId : null,
      );
      _applyCommittedItem(item);
      await refreshHistory(preferredEditId: item.editId, quiet: true);
      _setStatus('修圖完成', 'edit_complete');
      comparisonView = ComparisonView.result;
      return true;
    } on ApiException catch (error) {
      final isExpectedAdaptiveStop =
          error.statusCode == 409 &&
          (error.code == 'adaptive_feedback_satisfied' ||
              error.code == 'adaptive_step_converged');
      if (isExpectedAdaptiveStop) {
        _clearError();
        _setStatus(
          _friendlyApiMessage(error),
          error.code ?? 'adaptive_feedback_satisfied',
          details: error.details,
        );
      } else {
        _setApiError(error);
        _clearStatus();
      }
      return false;
    } catch (error) {
      _setError(
        '修圖失敗：$error',
        'edit_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      _clearStatus();
      return false;
    } finally {
      isProcessing = false;
      _notify();
    }
  }

  Future<bool> refreshHistory({
    String? preferredEditId,
    bool quiet = false,
  }) async {
    final id = sessionId;
    if (id == null) {
      return false;
    }
    try {
      final session = await _api.fetchSession(id);
      history = session.edits;
      final targetId =
          preferredEditId ??
          selectedEditId ??
          (history.isEmpty ? null : history.last.editId);
      if (targetId != null) {
        if (targetId == originalParentSentinel) {
          selectedEdit = null;
          selectedEditId = originalParentSentinel;
        } else {
          selectedEdit = _findEdit(targetId) ?? selectedEdit;
          selectedEditId = selectedEdit?.editId;
        }
      }
      originalImageUrl =
          selectedEdit?.originalUrl ??
          (history.isEmpty ? originalImageUrl : history.first.originalUrl);
      _ensureComparisonState();
      if (!quiet) {
        _setStatus('歷史紀錄已同步', 'history_synced');
      }
      _notify();
      return true;
    } on ApiException catch (error) {
      if (!quiet) {
        _setApiError(error);
        _notify();
      }
      return false;
    }
  }

  bool selectHistoryItem(EditHistoryItem item, {bool discardDraft = false}) {
    final switchingAwayFromPhotoGitTarget =
        hasPhotoGitDraft && selectedEditId != item.editId;
    if (((manualIsDirty && manualSourceEditId != item.editId) ||
            switchingAwayFromPhotoGitTarget) &&
        !discardDraft) {
      return false;
    }
    if (discardDraft && switchingAwayFromPhotoGitTarget) {
      _clearPhotoGitDraft(notify: false);
    }
    if (manualSourceEditId != item.editId) {
      _cancelPreview(clearDraft: true);
    }
    selectedEdit = item;
    selectedEditId = item.editId;
    originalImageUrl = item.originalUrl ?? originalImageUrl;
    comparisonView = ComparisonView.result;
    _ensureComparisonState();
    _clearError();
    _setStatus('已切換到歷史版本', 'switched_history');
    _notify();
    return true;
  }

  bool selectOriginalAsBase({bool discardDraft = false}) {
    if (hasPendingDraft && !discardDraft) {
      return false;
    }
    _cancelPreview(clearDraft: true);
    _clearPhotoGitDraft(notify: false);
    selectedEdit = null;
    selectedEditId = originalParentSentinel;
    comparisonView = ComparisonView.original;
    comparisonBaseline = ComparisonBaseline.original;
    _clearError();
    _setStatus('已切換到原圖，可建立新的歷史分支', 'switched_original');
    _notify();
    return true;
  }

  Future<bool> openManual() async {
    final edit = selectedEdit;
    if (edit == null || !canOpenManual) {
      _setError(manualDisabledReason, manualDisabledCode);
      _notify();
      return false;
    }
    activeTool = EditorTool.manual;
    if (manualSourceEditId == edit.editId && manualValues.isNotEmpty) {
      _notify();
      return true;
    }

    isLoadingManual = true;
    _clearError();
    _notify();
    try {
      manualSchema ??= await _api.fetchManualSchema();
      final schema = manualSchema!;
      manualSourceEditId = edit.editId;
      manualSourceValues = <String, double>{
        for (final spec in schema.parameters)
          spec.key: spec.normalize(
            edit.parameters[spec.key] is num
                ? (edit.parameters[spec.key] as num).toDouble()
                : spec.neutral,
          ),
      };
      manualValues = Map<String, double>.from(manualSourceValues);
      manualPreview = null;
      manualAdvancedExpanded = false;
      return true;
    } on ApiException catch (error) {
      _setApiError(error);
      return false;
    } catch (error) {
      _setError(
        '無法開啟手動調整：$error',
        'open_manual_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      return false;
    } finally {
      isLoadingManual = false;
      _notify();
    }
  }

  void setManualAdvancedExpanded(bool value) {
    manualAdvancedExpanded = value;
    _notify();
  }

  void setManualValue(ManualParameterSpec spec, double value) {
    if (manualSourceEditId == null) {
      return;
    }
    manualValues[spec.key] = spec.normalize(value);
    _clearError();
    _clearStatus();
    _scheduleManualPreview();
    _notify();
  }

  void resetManualParameter(ManualParameterSpec spec) {
    setManualValue(spec, spec.neutral);
  }

  void resetAllManual() {
    if (manualSourceValues.isEmpty) {
      return;
    }
    manualValues = Map<String, double>.from(manualSourceValues);
    _cancelPreview(clearDraft: false);
    manualPreview = null;
    _clearError();
    _setStatus('已回到來源版本參數', 'reset_source_parameters');
    _notify();
  }

  void discardManualDraft() {
    resetAllManual();
  }

  Map<String, double> get manualOverrides {
    final overrides = <String, double>{};
    for (final entry in manualValues.entries) {
      final source = manualSourceValues[entry.key];
      if (source == null || (source - entry.value).abs() > 0.000001) {
        overrides[entry.key] = entry.value;
      }
    }
    return overrides;
  }

  void _scheduleManualPreview() {
    _previewTimer?.cancel();
    _previewClient?.close();
    _previewClient = null;
    final sequence = ++_previewSequence;

    if (!manualIsDirty) {
      manualPreview = null;
      isPreviewing = false;
      return;
    }

    isPreviewing = true;
    _previewTimer = Timer(previewDebounce, () => _runManualPreview(sequence));
  }

  Future<void> _runManualPreview(int sequence) async {
    final currentSession = sessionId;
    final sourceEdit = manualSourceEditId;
    if (currentSession == null ||
        sourceEdit == null ||
        sequence != _previewSequence ||
        !manualIsDirty) {
      return;
    }

    final client = http.Client();
    _previewClient = client;
    try {
      final response = await _api.previewManual(
        sessionId: currentSession,
        sourceEditId: sourceEdit,
        parameterOverrides: manualOverrides,
        clientRequestId: 'flutter_preview_$sequence',
        requestClient: client,
      );
      if (sequence != _previewSequence ||
          sourceEdit != manualSourceEditId ||
          response.clientRequestId != 'flutter_preview_$sequence') {
        return;
      }
      manualPreview = response;
      _ensureComparisonState();
      _clearError();
    } on ApiException catch (error) {
      if (sequence == _previewSequence) {
        _setApiError(error);
      }
    } catch (error) {
      if (sequence == _previewSequence) {
        _setError(
          '手動預覽失敗：$error',
          'manual_preview_failed',
          arguments: <String, Object?>{'error': error.runtimeType.toString()},
        );
      }
    } finally {
      if (identical(_previewClient, client)) {
        _previewClient = null;
      }
      client.close();
      if (sequence == _previewSequence) {
        isPreviewing = false;
        _notify();
      }
    }
  }

  Future<bool> commitManual() async {
    final currentSession = sessionId;
    final sourceEdit = manualSourceEditId;
    if (currentSession == null || sourceEdit == null || !manualIsDirty) {
      return false;
    }
    _previewTimer?.cancel();
    _previewClient?.close();
    _previewClient = null;
    ++_previewSequence;
    isPreviewing = false;
    isCommittingManual = true;
    _clearError();
    _setStatus('正在套用手動調整…', 'applying_manual');
    _notify();

    try {
      final response = await _api.commitManual(
        sessionId: currentSession,
        sourceEditId: sourceEdit,
        parameterOverrides: manualOverrides,
        clientRequestId: 'flutter_commit_$_previewSequence',
      );
      final item = response.toHistoryItem();
      _applyCommittedItem(item);
      await refreshHistory(preferredEditId: item.editId, quiet: true);
      final committed = selectedEdit ?? item;
      manualSourceEditId = committed.editId;
      final schema = manualSchema;
      if (schema != null) {
        manualSourceValues = <String, double>{
          for (final spec in schema.parameters)
            spec.key: spec.normalize(
              committed.parameters[spec.key] is num
                  ? (committed.parameters[spec.key] as num).toDouble()
                  : spec.neutral,
            ),
        };
        manualValues = Map<String, double>.from(manualSourceValues);
      }
      manualPreview = null;
      _setStatus('手動調整已套用並加入歷史', 'manual_committed');
      comparisonView = ComparisonView.result;
      return true;
    } on ApiException catch (error) {
      _setApiError(error);
      _clearStatus();
      return false;
    } catch (error) {
      _setError(
        '手動調整套用失敗：$error',
        'manual_commit_failed',
        arguments: <String, Object?>{'error': error.runtimeType.toString()},
      );
      _clearStatus();
      return false;
    } finally {
      isCommittingManual = false;
      _notify();
    }
  }

  void _applyCommittedItem(EditHistoryItem item) {
    sessionId = item.sessionId;
    selectedEditId = item.editId;
    selectedEdit = item;
    originalImageUrl = item.originalUrl ?? originalImageUrl;
    final existingIndex = history.indexWhere(
      (entry) => entry.editId == item.editId,
    );
    if (existingIndex < 0) {
      history = <EditHistoryItem>[...history, item];
    } else {
      history = <EditHistoryItem>[...history]..[existingIndex] = item;
    }
    manualPreview = null;
    _ensureComparisonState();
  }

  EditHistoryItem? _findEdit(String editId) {
    for (final edit in history) {
      if (edit.editId == editId) {
        return edit;
      }
    }
    return null;
  }

  void _ensureComparisonState() {
    if (!hasResult && comparisonView != ComparisonView.original) {
      comparisonView = ComparisonView.original;
    }
    if (comparisonBaseline == ComparisonBaseline.parent &&
        !canCompareWithParent) {
      comparisonBaseline = ComparisonBaseline.original;
    }
  }

  String _friendlyApiMessage(ApiException error) {
    switch (error.code) {
      case 'style_selection_ambiguous':
        return '這段描述同時符合多個風格，請從風格目錄指定一個名稱。';
      case 'style_compound_not_supported':
        return '請先套用風格，再用下一句調整亮度、色彩或其他參數。';
      case 'style_asset_invalid':
      case 'style_version_mismatch':
        return '風格資產或版本驗證失敗，未套用其他替代風格。';
      case 'semantic_target_not_found':
        return '照片中找不到指定的局部範圍。請換一張圖片，或改用全圖調整。';
      case 'adaptive_clarification_required':
        return _structuredAdaptiveMessage(
          error,
          fallback: '我不確定要微調哪一項，請補充參數或指定區域。',
        );
      case 'adaptive_step_converged':
        return _structuredAdaptiveMessage(
          error,
          fallback: '這個方向的調整已接近目前最小步長；可改用手動參數做最後微調。',
          preferFallback: true,
        );
      case 'adaptive_feedback_satisfied':
        return _structuredAdaptiveMessage(
          error,
          fallback: '已保留目前結果，沒有新增重複的歷史版本。',
          preferFallback: true,
        );
      case 'manual_source_mode_unsupported':
        return '參考圖結果目前不能手動調整，請選擇指令或手動版本。';
      case 'photo_git_scope_required':
      case 'photo_git_scope_unclear':
      case 'photo_git_scope_ambiguous':
        return '請用文字或選項指定要操作的區域或參數。';
      case 'photo_git_conflict':
        return '仍有版本衝突未決定，請逐項選擇後再試一次。';
      case 'photo_git_plan_stale':
        return '版本內容已變更，請重新分析後再預覽。';
      case 'photo_git_no_change':
        return '所選內容與目前版本相同，不會建立重複版本。';
      case 'photo_git_version_unsupported':
      case 'photo_git_recipe_unsupported':
        return '這個版本缺少可安全重算的來源資訊，無法進行此操作。';
      case 'network_error':
        return '無法連線到修圖後端，請確認後端已啟動。';
      default:
        if (error.statusCode == 422 && error.details.isNotEmpty) {
          return _structuredAdaptiveMessage(error);
        }
        return error.message;
    }
  }

  String _structuredAdaptiveMessage(
    ApiException error, {
    String? fallback,
    bool preferFallback = false,
  }) {
    final backendMessage = error.message.trim();
    final base = preferFallback && fallback != null
        ? fallback
        : backendMessage.isEmpty
        ? (fallback ?? '請確認修圖指令後再試一次。')
        : backendMessage;
    final rawIssues = error.details['issues'];
    if (rawIssues is! List) {
      return base;
    }
    final contexts = <String>[];
    for (final value in rawIssues) {
      if (value is! Map) {
        continue;
      }
      final issue = Map<String, dynamic>.from(value);
      final axis = issue['axis']?.toString();
      final region = issue['region']?.toString();
      final sourceClause = issue['source_clause']?.toString().trim();
      final parts = <String>[
        if (axis != null && axis.isNotEmpty)
          parameterMetadataCatalog.labelFor(axis),
        if (region != null && region.isNotEmpty) regionLabel(region),
        if (sourceClause != null && sourceClause.isNotEmpty) '「$sourceClause」',
      ];
      final context = parts.join(' ');
      if (context.isNotEmpty && !contexts.contains(context)) {
        contexts.add(context);
      }
    }
    return contexts.isEmpty ? base : '$base（涉及：${contexts.join('、')}）';
  }

  PhotoGitRequest? _buildPhotoGitRequest() {
    final targetId = selectedEditId;
    if (targetId == null || targetId == originalParentSentinel) {
      return null;
    }
    return PhotoGitRequest(
      operation: photoGitOperation,
      targetEditId: targetId,
      sourceEditId: photoGitOperation == PhotoGitOperation.merge
          ? photoGitSourceEditId
          : null,
      revertEditId: photoGitOperation == PhotoGitOperation.selectiveRevert
          ? photoGitRevertEditId
          : null,
      instruction: photoGitInstruction,
      selectors: photoGitSelectors,
      resolutions: photoGitResolutions,
    );
  }

  void _invalidatePhotoGitPlan() {
    photoGitPlan = null;
    photoGitPreview = null;
    photoGitResolutions = <String, String>{};
    _photoGitClientRequestId = null;
    comparisonView = selectedEdit == null
        ? ComparisonView.original
        : ComparisonView.result;
    _ensureComparisonState();
  }

  void _clearPhotoGitDraft({required bool notify}) {
    photoGitOperation = PhotoGitOperation.merge;
    photoGitInstruction = '';
    photoGitSourceEditId = null;
    photoGitRevertEditId = null;
    photoGitRegion = null;
    photoGitParameter = null;
    photoGitResolutions = <String, String>{};
    photoGitPlan = null;
    photoGitPreview = null;
    isPlanningPhotoGit = false;
    isPreviewingPhotoGit = false;
    isCommittingPhotoGit = false;
    _photoGitClientRequestId = null;
    if (notify) {
      _notify();
    }
  }

  static bool _isPhotoGitCapableMode(String editMode) {
    return editMode == 'prompt' ||
        editMode == 'manual' ||
        editMode == 'photo_git_merge' ||
        editMode == 'photo_git_revert';
  }

  void _cancelPreview({required bool clearDraft}) {
    _previewTimer?.cancel();
    _previewTimer = null;
    _previewClient?.close();
    _previewClient = null;
    ++_previewSequence;
    isPreviewing = false;
    manualPreview = null;
    if (clearDraft) {
      manualSourceEditId = null;
      manualSourceValues = <String, double>{};
      manualValues = <String, double>{};
      manualAdvancedExpanded = false;
    }
    _ensureComparisonState();
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _cancelPreview(clearDraft: true);
    _clearPhotoGitDraft(notify: false);
    if (_api case final ApiService service) {
      service.close();
    }
    super.dispose();
  }
}
