import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'api_service.dart';

void main() {
  runApp(const MyApp());
}

String formatParserSourceLabel(String? source) {
  switch (source) {
    case 'llm':
      return 'LLM 解析';
    case 'rule_based_fallback':
      return '規則 fallback';
    case 'reference_mode':
      return '參考圖模式';
    case null:
      return '';
    default:
      return source;
  }
}

class EditHistoryItem {
  const EditHistoryItem({
    required this.editId,
    required this.parentEditId,
    required this.prompt,
    required this.resultUrl,
    required this.resolvedIntent,
    required this.editMode,
    required this.parserSource,
    required this.explanation,
    required this.parameters,
  });

  final String editId;
  final String? parentEditId;
  final String prompt;
  final String resultUrl;
  final String? resolvedIntent;
  final String? editMode;
  final String? parserSource;
  final String? explanation;
  final Map<String, dynamic>? parameters;

  factory EditHistoryItem.fromResponse(Map<String, dynamic> result) {
    final dynamic parameters = result['parameters'];
    return EditHistoryItem(
      editId: result['edit_id'] as String,
      parentEditId: result['parent_edit_id'] as String?,
      prompt: (result['prompt'] as String?) ?? '',
      resultUrl: ApiService.buildImageUrl(result['result_url'] as String),
      resolvedIntent: result['resolved_intent'] as String?,
      editMode: result['edit_mode'] as String?,
      parserSource: result['parser_source'] as String?,
      explanation: result['explanation'] as String?,
      parameters: parameters is Map
          ? Map<String, dynamic>.from(parameters)
          : null,
    );
  }
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    const Color seed = Color(0xFF3F6F8F);

    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AI Photo Editor',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF4F6F8),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final ImagePicker _picker = ImagePicker();
  final TextEditingController _promptController = TextEditingController();

  Uint8List? _originalImageBytes;
  Uint8List? _referenceImageBytes;

  String? _resultImageUrl;
  String? _resultExplanation;
  String? _resolvedIntent;
  String? _editMode;
  String? _parserSource;
  Map<String, dynamic>? _resultParameters;
  String? _sessionId;
  String? _latestEditId;
  final List<EditHistoryItem> _editHistory = [];

  bool _isProcessing = false;
  String? _errorMessage;

  static const double _wideBreakpoint = 1500;
  static const double _mediumBreakpoint = 760;
  static const Color _panelBorder = Color(0xFFD8DEE6);
  static const Color _panelBackground = Color(0xFFFFFFFF);
  static const Color _imageStage = Color(0xFF121820);
  static const Color _mutedText = Color(0xFF607080);

  @override
  void dispose() {
    _promptController.dispose();
    super.dispose();
  }

  Future<void> _pickImage({required bool isOriginal}) async {
    final XFile? pickedFile = await _picker.pickImage(
      source: ImageSource.gallery,
    );

    if (pickedFile == null) {
      return;
    }

    final Uint8List bytes = await pickedFile.readAsBytes();

    setState(() {
      if (isOriginal) {
        _originalImageBytes = bytes;
        _clearSessionState();
      } else {
        _referenceImageBytes = bytes;
        _clearSessionState();
      }
      _clearResultState();
      _errorMessage = null;
    });
  }

  void _clearImage({required bool isOriginal}) {
    setState(() {
      if (isOriginal) {
        _originalImageBytes = null;
        _clearSessionState();
      } else {
        _referenceImageBytes = null;
      }
      _clearResultState();
      _errorMessage = null;
    });
  }

  void _clearResultState() {
    _resultImageUrl = null;
    _resultExplanation = null;
    _resolvedIntent = null;
    _editMode = null;
    _parserSource = null;
    _resultParameters = null;
  }

  void _clearSessionState() {
    _sessionId = null;
    _latestEditId = null;
    _editHistory.clear();
  }

  Future<void> _startEdit() async {
    final String prompt = _promptController.text.trim();
    final bool hasPrompt = prompt.isNotEmpty;
    final bool hasReference = _referenceImageBytes != null;
    final bool canContinueFromLatest =
        _sessionId != null &&
        _latestEditId != null &&
        hasPrompt &&
        !hasReference;

    if (_originalImageBytes == null && !canContinueFromLatest) {
      return;
    }

    if (hasPrompt == hasReference) {
      setState(() {
        _errorMessage = hasPrompt ? '請使用文字修圖或參考圖修圖其中一種。' : '請輸入修圖需求或選擇參考圖片。';
      });
      return;
    }

    setState(() {
      _isProcessing = true;
      _clearResultState();
      _errorMessage = null;
    });

    try {
      final result = await ApiService.uploadImages(
        originalBytes: canContinueFromLatest ? null : _originalImageBytes!,
        referenceBytes: _referenceImageBytes,
        prompt: prompt,
        sessionId: canContinueFromLatest ? _sessionId : null,
        parentEditId: canContinueFromLatest ? _latestEditId : null,
      );

      final String resultUrl = ApiService.buildImageUrl(
        result['result_url'] as String,
      );
      final dynamic parameters = result['parameters'];
      final historyItem = EditHistoryItem.fromResponse(result);

      setState(() {
        _resultImageUrl = resultUrl;
        _resultExplanation = result['explanation'] as String?;
        _resolvedIntent = result['resolved_intent'] as String?;
        _editMode = result['edit_mode'] as String?;
        _parserSource = result['parser_source'] as String?;
        _resultParameters = parameters is Map
            ? Map<String, dynamic>.from(parameters)
            : null;
        _sessionId = result['session_id'] as String?;
        _latestEditId = result['edit_id'] as String?;
        _editHistory.add(historyItem);
      });
    } catch (e) {
      setState(() {
        _errorMessage = '修圖失敗：$e';
      });
    } finally {
      setState(() {
        _isProcessing = false;
      });
    }
  }

  String _formatParameters(Map<String, dynamic> parameters) {
    return parameters.entries
        .map((entry) => '${entry.key}: ${entry.value}')
        .join('\n');
  }

  String _formatCompactParameters(Map<String, dynamic> parameters) {
    return parameters.entries
        .map((entry) => '${entry.key} ${entry.value}')
        .join(' · ');
  }

  bool get _canStartEdit {
    final bool hasPrompt = _promptController.text.trim().isNotEmpty;
    final bool hasReference = _referenceImageBytes != null;
    final bool hasOneEditMode = hasPrompt != hasReference;
    final bool canContinueFromLatest =
        _sessionId != null &&
        _latestEditId != null &&
        hasPrompt &&
        !hasReference;

    return (_originalImageBytes != null || canContinueFromLatest) &&
        hasOneEditMode &&
        !_isProcessing;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final bool isWide = constraints.maxWidth >= _wideBreakpoint;
            final bool isMedium = constraints.maxWidth >= _mediumBreakpoint;

            if (isWide) {
              return _buildWideWorkspace(constraints);
            }

            return _buildStackedWorkspace(isMedium: isMedium);
          },
        ),
      ),
    );
  }

  Widget _buildWideWorkspace(BoxConstraints constraints) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _buildHeader(isCompact: false),
                const SizedBox(height: 12),
                Expanded(child: _buildComparisonWorkspace(isWide: true)),
                const SizedBox(height: 12),
                _buildBottomControls(isWide: true),
              ],
            ),
          ),
          const SizedBox(width: 16),
          SizedBox(
            width: constraints.maxWidth >= 1380 ? 360 : 320,
            child: _buildHistoryPanel(fillHeight: true),
          ),
        ],
      ),
    );
  }

  Widget _buildStackedWorkspace({required bool isMedium}) {
    return SingleChildScrollView(
      padding: EdgeInsets.all(isMedium ? 16 : 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildHeader(isCompact: !isMedium),
          const SizedBox(height: 12),
          _buildComparisonWorkspace(isWide: false, imagesSideBySide: isMedium),
          const SizedBox(height: 12),
          _buildBottomControls(isWide: false),
          const SizedBox(height: 12),
          SizedBox(
            height: isMedium ? 360 : null,
            child: _buildHistoryPanel(fillHeight: isMedium),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader({required bool isCompact}) {
    return _panel(
      padding: EdgeInsets.symmetric(
        horizontal: isCompact ? 14 : 18,
        vertical: isCompact ? 12 : 14,
      ),
      child: Row(
        children: [
          Container(
            width: 42,
            height: 42,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: const Color(0xFFE5EEF5),
              borderRadius: BorderRadius.circular(8),
            ),
            child: const Icon(Icons.auto_fix_high, color: Color(0xFF24516B)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'AI Photo Editor',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 3),
                Text(
                  isCompact ? '自然語言修圖測試台' : '自然語言修圖、版本續修與結果比較測試台',
                  style: const TextStyle(fontSize: 13, color: _mutedText),
                ),
              ],
            ),
          ),
          if (_sessionId != null)
            _statusChip(
              icon: Icons.timeline,
              label: 'Session ${_shortId(_sessionId!)}',
            ),
        ],
      ),
    );
  }

  Widget _buildComparisonWorkspace({
    required bool isWide,
    bool imagesSideBySide = false,
  }) {
    return _panel(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.compare, size: 20, color: Color(0xFF24516B)),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  '原圖 / 結果比較',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
              ),
              if (_latestEditId != null)
                _statusChip(
                  icon: Icons.adjust,
                  label: '目前基準 ${_shortId(_latestEditId!)}',
                ),
            ],
          ),
          const SizedBox(height: 12),
          if (isWide)
            Expanded(child: _buildWideComparisonBody())
          else ...[
            if (imagesSideBySide)
              SizedBox(height: 380, child: _buildImagePair(isWide: true))
            else
              _buildImagePair(isWide: false),
            const SizedBox(height: 12),
            _buildAdjustmentSummary(fillHeight: false),
          ],
        ],
      ),
    );
  }

  Widget _buildWideComparisonBody() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(child: _buildImagePair(isWide: true)),
        const SizedBox(width: 12),
        SizedBox(width: 360, child: _buildAdjustmentSummary(fillHeight: true)),
      ],
    );
  }

  Widget _buildImagePair({required bool isWide}) {
    final Widget originalPane = _buildImagePane(
      title: '原圖',
      badge: _originalImageBytes == null ? '待上傳' : '已選擇',
      child: _originalImageBytes == null
          ? _buildImagePlaceholder(
              icon: Icons.add_photo_alternate_outlined,
              title: '選擇原始圖片',
              detail: '支援直圖、橫圖與正方形，畫面會自動等比例顯示。',
              onPressed: () => _pickImage(isOriginal: true),
            )
          : _buildMemoryImage(_originalImageBytes!),
      action: _originalImageBytes == null
          ? null
          : IconButton.filledTonal(
              tooltip: '清除原圖',
              onPressed: () => _clearImage(isOriginal: true),
              icon: const Icon(Icons.close),
            ),
    );

    final Widget resultPane = _buildImagePane(
      title: '結果圖',
      badge: _resultImageUrl == null ? '等待修圖' : '最新結果',
      child: _resultImageUrl == null
          ? _buildResultPlaceholder()
          : Image.network(
              _resultImageUrl!,
              fit: BoxFit.contain,
              errorBuilder: (context, error, stackTrace) {
                return const Center(
                  child: Text(
                    '結果圖片載入失敗',
                    style: TextStyle(color: Colors.white70),
                  ),
                );
              },
            ),
    );

    if (isWide) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(child: originalPane),
          const SizedBox(width: 12),
          Expanded(child: resultPane),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(height: 320, child: originalPane),
        const SizedBox(height: 12),
        SizedBox(height: 320, child: resultPane),
      ],
    );
  }

  Widget _buildImagePane({
    required String title,
    required String badge,
    required Widget child,
    Widget? action,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: _imageStage,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF263341)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        children: [
          Positioned.fill(child: Center(child: child)),
          Positioned(
            left: 10,
            top: 10,
            child: _imageLabel(title: title, badge: badge),
          ),
          if (action != null) Positioned(right: 10, top: 10, child: action),
        ],
      ),
    );
  }

  Widget _buildMemoryImage(Uint8List bytes) {
    return Image.memory(bytes, fit: BoxFit.contain, gaplessPlayback: true);
  }

  Widget _buildImagePlaceholder({
    required IconData icon,
    required String title,
    required String detail,
    required VoidCallback onPressed,
  }) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 42, color: Colors.white70),
          const SizedBox(height: 12),
          Text(
            title,
            textAlign: TextAlign.center,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 17,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 8),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 360),
            child: Text(
              detail,
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white60, fontSize: 13),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: onPressed,
            icon: const Icon(Icons.upload_file),
            label: const Text('選擇圖片'),
          ),
        ],
      ),
    );
  }

  Widget _buildResultPlaceholder() {
    if (_isProcessing) {
      return const Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(color: Colors.white),
          SizedBox(height: 14),
          Text('修圖中，請稍候...', style: TextStyle(color: Colors.white70)),
        ],
      );
    }

    return const Padding(
      padding: EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.image_search_outlined, size: 42, color: Colors.white70),
          SizedBox(height: 12),
          Text(
            '尚未產生結果',
            style: TextStyle(
              color: Colors.white,
              fontSize: 17,
              fontWeight: FontWeight.w700,
            ),
          ),
          SizedBox(height: 8),
          Text(
            '輸入 prompt 或選擇參考圖後，結果會在這裡和原圖並排比較。',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white60, fontSize: 13),
          ),
        ],
      ),
    );
  }

  Widget _buildBottomControls({required bool isWide}) {
    final Widget promptCard = _buildPromptCard();
    final Widget referenceCard = _buildReferenceCard();

    if (isWide) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(flex: 3, child: promptCard),
          const SizedBox(width: 12),
          Expanded(flex: 2, child: referenceCard),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [promptCard, const SizedBox(height: 12), referenceCard],
    );
  }

  Widget _buildPromptCard() {
    final String prompt = _promptController.text.trim();
    final bool hasPrompt = prompt.isNotEmpty;
    final bool hasReference = _referenceImageBytes != null;
    final bool hasConflict = hasPrompt && hasReference;
    final String helperText = hasConflict
        ? '目前同時有 prompt 和參考圖，請保留其中一種。'
        : '文字修圖和參考圖修圖請擇一使用。';

    return _panel(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            '修圖需求',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _promptController,
            minLines: 1,
            maxLines: 3,
            textInputAction: TextInputAction.done,
            onChanged: (_) {
              setState(() {
                _errorMessage = null;
              });
            },
            decoration: InputDecoration(
              hintText: '例如：幫我調亮一點、暖一點、更鮮豔',
              filled: true,
              fillColor: const Color(0xFFF8FAFC),
              prefixIcon: const Icon(Icons.chat_bubble_outline),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            helperText,
            style: TextStyle(
              color: hasConflict ? const Color(0xFFC62828) : _mutedText,
              fontSize: 13,
            ),
          ),
          if (_errorMessage != null) ...[
            const SizedBox(height: 8),
            Text(
              _errorMessage!,
              style: const TextStyle(fontSize: 13, color: Color(0xFFC62828)),
            ),
          ],
          const SizedBox(height: 12),
          SizedBox(
            height: 46,
            child: FilledButton.icon(
              onPressed: _canStartEdit ? _startEdit : null,
              icon: Icon(
                _isProcessing ? Icons.hourglass_top : Icons.auto_fix_high,
              ),
              label: Text(_isProcessing ? '修圖中...' : '開始修圖'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildReferenceCard() {
    return _panel(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            '參考圖',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          Container(
            height: 118,
            decoration: BoxDecoration(
              color: const Color(0xFFF8FAFC),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: _panelBorder),
            ),
            clipBehavior: Clip.antiAlias,
            child: _referenceImageBytes == null
                ? const Center(
                    child: Text(
                      '沒有參考圖時會使用 prompt 修圖',
                      style: TextStyle(color: _mutedText, fontSize: 13),
                    ),
                  )
                : Image.memory(_referenceImageBytes!, fit: BoxFit.contain),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => _pickImage(isOriginal: false),
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                  label: Text(_referenceImageBytes == null ? '選擇參考圖' : '更換參考圖'),
                ),
              ),
              if (_referenceImageBytes != null) ...[
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: '清除參考圖',
                  onPressed: () => _clearImage(isOriginal: false),
                  icon: const Icon(Icons.close),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildAdjustmentSummary({required bool fillHeight}) {
    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.tune, size: 18, color: Color(0xFF24516B)),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                '本次調整',
                style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
              ),
            ),
            if (_parserSource != null)
              _statusChip(
                icon: Icons.psychology_outlined,
                label: formatParserSourceLabel(_parserSource),
              ),
          ],
        ),
        const SizedBox(height: 8),
        if (_resultExplanation == null && _resultParameters == null)
          const Text(
            '完成修圖後，解析來源、調整說明與參數會顯示在這裡，方便直接看圖比對。',
            style: TextStyle(color: _mutedText, fontSize: 13),
          )
        else ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (_resolvedIntent != null)
                _infoPill('Intent', _resolvedIntent!),
              if (_editMode != null) _infoPill('Mode', _editMode!),
              if (_latestEditId != null)
                _infoPill('Edit', _shortId(_latestEditId!)),
            ],
          ),
          if (_resultExplanation != null) ...[
            const SizedBox(height: 8),
            Text(_resultExplanation!, style: const TextStyle(fontSize: 13)),
          ],
          if (_resultParameters != null) ...[
            const SizedBox(height: 8),
            SelectableText(
              _formatParameters(_resultParameters!),
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 12,
                height: 1.35,
              ),
            ),
          ],
        ],
      ],
    );

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFF8FAFC),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _panelBorder),
      ),
      child: fillHeight ? SingleChildScrollView(child: content) : content,
    );
  }

  Widget _buildHistoryPanel({required bool fillHeight}) {
    final Widget content = _editHistory.isEmpty
        ? const Center(
            child: Padding(
              padding: EdgeInsets.all(18),
              child: Text(
                '修圖歷史會顯示在這裡。\n點選任一結果後，後續 prompt 會以該版本為基準。',
                textAlign: TextAlign.center,
                style: TextStyle(color: _mutedText, height: 1.45),
              ),
            ),
          )
        : fillHeight
        ? ListView.builder(
            padding: EdgeInsets.zero,
            itemCount: _editHistory.length,
            itemBuilder: (context, index) {
              return _buildHistoryTile(index: index, item: _editHistory[index]);
            },
          )
        : Column(
            children: [
              for (final entry in _editHistory.asMap().entries)
                _buildHistoryTile(index: entry.key, item: entry.value),
            ],
          );

    return _panel(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.history, size: 20, color: Color(0xFF24516B)),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  '修圖歷史',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
              ),
              Text(
                '${_editHistory.length}',
                style: const TextStyle(color: _mutedText, fontSize: 13),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (fillHeight) Expanded(child: content) else content,
        ],
      ),
    );
  }

  Widget _buildHistoryTile({
    required int index,
    required EditHistoryItem item,
  }) {
    final String titlePrompt = item.prompt.isEmpty ? '參考圖修圖' : item.prompt;
    final bool isSelected = item.editId == _latestEditId;
    final String subtitle = [
      if (item.resolvedIntent != null) item.resolvedIntent!,
      if (item.parserSource != null) formatParserSourceLabel(item.parserSource),
      if (item.parentEditId != null) 'parent ${_shortId(item.parentEditId!)}',
    ].join(' · ');

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () {
          setState(() {
            _resultImageUrl = item.resultUrl;
            _resultExplanation = item.explanation;
            _resolvedIntent = item.resolvedIntent;
            _editMode = item.editMode;
            _parserSource = item.parserSource;
            _resultParameters = item.parameters;
            _latestEditId = item.editId;
          });
        },
        child: Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: isSelected
                ? const Color(0xFFEAF3F8)
                : const Color(0xFFF8FAFC),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: isSelected ? const Color(0xFF6FA3BF) : _panelBorder,
            ),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: Image.network(
                  item.resultUrl,
                  width: 58,
                  height: 58,
                  fit: BoxFit.cover,
                  errorBuilder: (context, error, stackTrace) {
                    return Container(
                      width: 58,
                      height: 58,
                      color: const Color(0xFFE8EDF2),
                      alignment: Alignment.center,
                      child: const Icon(Icons.image_not_supported_outlined),
                    );
                  },
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          '#${index + 1}',
                          style: const TextStyle(
                            color: Color(0xFF24516B),
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        if (isSelected) ...[
                          const SizedBox(width: 6),
                          const Icon(
                            Icons.check_circle,
                            size: 15,
                            color: Color(0xFF2F7D52),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(
                      titlePrompt,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    if (subtitle.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        subtitle,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: _mutedText, fontSize: 12),
                      ),
                    ],
                    if (item.parameters != null) ...[
                      const SizedBox(height: 4),
                      Text(
                        _formatCompactParameters(item.parameters!),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: _mutedText, fontSize: 11),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _panel({required Widget child, required EdgeInsets padding}) {
    return Container(
      decoration: BoxDecoration(
        color: _panelBackground,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _panelBorder),
        boxShadow: const [
          BoxShadow(
            color: Color(0x12000000),
            blurRadius: 18,
            offset: Offset(0, 8),
          ),
        ],
      ),
      padding: padding,
      child: child,
    );
  }

  Widget _imageLabel({required String title, required String badge}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: const Color(0xDD0F1720),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            title,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w700,
              fontSize: 13,
            ),
          ),
          const SizedBox(width: 8),
          Text(
            badge,
            style: const TextStyle(color: Colors.white60, fontSize: 12),
          ),
        ],
      ),
    );
  }

  Widget _statusChip({required IconData icon, required String label}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
      decoration: BoxDecoration(
        color: const Color(0xFFEAF3F8),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFC9DDE8)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 15, color: const Color(0xFF24516B)),
          const SizedBox(width: 5),
          Text(
            label,
            style: const TextStyle(
              color: Color(0xFF24516B),
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _infoPill(String label, String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _panelBorder),
      ),
      child: Text(
        '$label: $value',
        style: const TextStyle(fontSize: 12, color: Color(0xFF2B3A48)),
      ),
    );
  }

  String _shortId(String id) {
    if (id.length <= 8) {
      return id;
    }
    return id.substring(id.length - 8);
  }
}
