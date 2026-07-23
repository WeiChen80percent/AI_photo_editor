import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'api_service.dart';
import 'edit_models.dart';

enum EditorTool { prompt, reference, manual, history }

enum ComparisonView { original, result }

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

  bool isProcessing = false;
  String? errorMessage;
  String? statusMessage;

  ManualSchema? manualSchema;
  String? manualSourceEditId;
  Map<String, double> manualSourceValues = <String, double>{};
  Map<String, double> manualValues = <String, double>{};
  ManualEditResponse? manualPreview;
  bool isLoadingManual = false;
  bool isPreviewing = false;
  bool isCommittingManual = false;
  bool manualAdvancedExpanded = false;

  Timer? _previewTimer;
  http.Client? _previewClient;
  int _previewSequence = 0;
  bool _disposed = false;

  bool get hasOriginal =>
      originalImageBytes != null || originalImageUrl != null;

  bool get hasResult => currentResultUrl != null;

  String? get currentResultUrl =>
      manualPreview?.resultUrl ?? selectedEdit?.resultUrl;

  bool get isOriginalBaseSelected =>
      selectedEdit == null && selectedEditId == originalParentSentinel;

  Map<String, dynamic> get currentParameters =>
      manualPreview?.parameters ?? selectedEdit?.parameters ?? const {};

  bool get hasUncommittedPreview => manualPreview != null && manualIsDirty;

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
      promptDraft.trim().isNotEmpty &&
      (hasOriginal || selectedEdit != null);

  bool get canSubmitReference =>
      !isProcessing &&
      referenceImageBytes != null &&
      (hasOriginal || selectedEdit != null);

  bool get canOpenManual {
    final edit = selectedEdit;
    return edit != null &&
        edit.engine.toLowerCase() == 'opencv' &&
        (edit.editMode == 'prompt' || edit.editMode == 'manual');
  }

  String get manualDisabledReason {
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
    final parameters = compactParameterSummary(currentParameters);
    final previewLabel = hasUncommittedPreview ? '預覽 · ' : '';
    if (parameters.isEmpty) {
      return '$previewLabel$target · ${edit.modeLabel}';
    }
    return '$previewLabel$target · $parameters';
  }

  void setActiveTool(EditorTool? tool) {
    activeTool = tool;
    clearMessages();
    _notify();
  }

  void setComparisonView(ComparisonView view) {
    comparisonView = view;
    _notify();
  }

  void setPromptDraft(String value) {
    promptDraft = value;
    errorMessage = null;
    _notify();
  }

  void setOriginalImage(Uint8List bytes) {
    _cancelPreview(clearDraft: true);
    originalImageBytes = bytes;
    originalImageUrl = null;
    referenceImageBytes = null;
    sessionId = null;
    selectedEditId = null;
    selectedEdit = null;
    history = <EditHistoryItem>[];
    comparisonView = ComparisonView.result;
    errorMessage = null;
    statusMessage = '已選擇新的原始圖片';
    _notify();
  }

  void clearOriginalImage() {
    _cancelPreview(clearDraft: true);
    originalImageBytes = null;
    originalImageUrl = null;
    referenceImageBytes = null;
    sessionId = null;
    selectedEditId = null;
    selectedEdit = null;
    history = <EditHistoryItem>[];
    errorMessage = null;
    statusMessage = null;
    _notify();
  }

  void setReferenceImage(Uint8List bytes) {
    referenceImageBytes = bytes;
    errorMessage = null;
    statusMessage = '參考圖已準備完成';
    _notify();
  }

  void clearReferenceImage() {
    referenceImageBytes = null;
    errorMessage = null;
    _notify();
  }

  void clearMessages() {
    errorMessage = null;
    statusMessage = null;
  }

  Future<bool> submitPrompt() async {
    final prompt = promptDraft.trim();
    if (prompt.isEmpty) {
      errorMessage = '請輸入修圖指令。';
      _notify();
      return false;
    }
    return _submitEdit(prompt: prompt, referenceBytes: null);
  }

  Future<bool> submitReference() async {
    final reference = referenceImageBytes;
    if (reference == null) {
      errorMessage = '請先選擇參考圖片。';
      _notify();
      return false;
    }
    return _submitEdit(prompt: '', referenceBytes: reference);
  }

  Future<bool> _submitEdit({
    required String prompt,
    required Uint8List? referenceBytes,
  }) async {
    if (isProcessing) {
      return false;
    }
    final canContinue = sessionId != null && selectedEditId != null;
    if (!canContinue && originalImageBytes == null) {
      errorMessage = '請先選擇原始圖片。';
      _notify();
      return false;
    }

    isProcessing = true;
    errorMessage = null;
    statusMessage = referenceBytes == null ? '正在解析修圖指令…' : '正在套用參考圖…';
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
      statusMessage = '修圖完成';
      comparisonView = ComparisonView.result;
      return true;
    } on ApiException catch (error) {
      final isExpectedAdaptiveStop =
          error.statusCode == 409 &&
          (error.code == 'adaptive_feedback_satisfied' ||
              error.code == 'adaptive_step_converged');
      if (isExpectedAdaptiveStop) {
        errorMessage = null;
        statusMessage = _friendlyApiMessage(error);
      } else {
        errorMessage = _friendlyApiMessage(error);
        statusMessage = null;
      }
      return false;
    } catch (error) {
      errorMessage = '修圖失敗：$error';
      statusMessage = null;
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
      if (!quiet) {
        statusMessage = '歷史紀錄已同步';
      }
      _notify();
      return true;
    } on ApiException catch (error) {
      if (!quiet) {
        errorMessage = _friendlyApiMessage(error);
        _notify();
      }
      return false;
    }
  }

  bool selectHistoryItem(EditHistoryItem item, {bool discardDraft = false}) {
    if (manualIsDirty && manualSourceEditId != item.editId && !discardDraft) {
      return false;
    }
    if (manualSourceEditId != item.editId) {
      _cancelPreview(clearDraft: true);
    }
    selectedEdit = item;
    selectedEditId = item.editId;
    originalImageUrl = item.originalUrl ?? originalImageUrl;
    comparisonView = ComparisonView.result;
    errorMessage = null;
    statusMessage = '已切換到歷史版本';
    _notify();
    return true;
  }

  bool selectOriginalAsBase({bool discardDraft = false}) {
    if (manualIsDirty && !discardDraft) {
      return false;
    }
    _cancelPreview(clearDraft: true);
    selectedEdit = null;
    selectedEditId = originalParentSentinel;
    comparisonView = ComparisonView.original;
    errorMessage = null;
    statusMessage = '已切換到原圖，可建立新的歷史分支';
    _notify();
    return true;
  }

  Future<bool> openManual() async {
    final edit = selectedEdit;
    if (edit == null || !canOpenManual) {
      errorMessage = manualDisabledReason;
      _notify();
      return false;
    }
    activeTool = EditorTool.manual;
    if (manualSourceEditId == edit.editId && manualValues.isNotEmpty) {
      _notify();
      return true;
    }

    isLoadingManual = true;
    errorMessage = null;
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
      errorMessage = _friendlyApiMessage(error);
      return false;
    } catch (error) {
      errorMessage = '無法開啟手動調整：$error';
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
    errorMessage = null;
    statusMessage = null;
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
    errorMessage = null;
    statusMessage = '已回到來源版本參數';
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
      errorMessage = null;
    } on ApiException catch (error) {
      if (sequence == _previewSequence) {
        errorMessage = _friendlyApiMessage(error);
      }
    } catch (error) {
      if (sequence == _previewSequence) {
        errorMessage = '手動預覽失敗：$error';
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
    errorMessage = null;
    statusMessage = '正在套用手動調整…';
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
      statusMessage = '手動調整已套用並加入歷史';
      comparisonView = ComparisonView.result;
      return true;
    } on ApiException catch (error) {
      errorMessage = _friendlyApiMessage(error);
      statusMessage = null;
      return false;
    } catch (error) {
      errorMessage = '手動調整套用失敗：$error';
      statusMessage = null;
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
  }

  EditHistoryItem? _findEdit(String editId) {
    for (final edit in history) {
      if (edit.editId == editId) {
        return edit;
      }
    }
    return null;
  }

  String _friendlyApiMessage(ApiException error) {
    switch (error.code) {
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
        if (axis != null && axis.isNotEmpty) parameterLabels[axis] ?? axis,
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
    if (_api case final ApiService service) {
      service.close();
    }
    super.dispose();
  }
}
