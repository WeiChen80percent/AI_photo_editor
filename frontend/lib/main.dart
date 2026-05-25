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
    required this.parserSource,
    required this.parameters,
  });

  final String editId;
  final String? parentEditId;
  final String prompt;
  final String resultUrl;
  final String? resolvedIntent;
  final String? parserSource;
  final Map<String, dynamic>? parameters;

  factory EditHistoryItem.fromResponse(Map<String, dynamic> result) {
    final dynamic parameters = result['parameters'];
    return EditHistoryItem(
      editId: result['edit_id'] as String,
      parentEditId: result['parent_edit_id'] as String?,
      prompt: (result['prompt'] as String?) ?? '',
      resultUrl: ApiService.buildImageUrl(result['result_url'] as String),
      resolvedIntent: result['resolved_intent'] as String?,
      parserSource: result['parser_source'] as String?,
      parameters: parameters is Map ? Map<String, dynamic>.from(parameters) : null,
    );
  }
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AI Photo Editor',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
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
        _sessionId != null && _latestEditId != null && hasPrompt && !hasReference;

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

  Widget _buildImageSection({
    required String title,
    required Uint8List? imageBytes,
    required VoidCallback onPick,
    required VoidCallback onClear,
    required String buttonText,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 12),
        _buildPreviewFrame(
          child: Stack(
            fit: StackFit.expand,
            children: [
              if (imageBytes == null)
                const Center(
                  child: Text('尚未選擇圖片', style: TextStyle(fontSize: 18)),
                )
              else
                Image.memory(imageBytes, fit: BoxFit.contain),
              if (imageBytes != null)
                Positioned(
                  top: 8,
                  right: 8,
                  child: Tooltip(
                    message: '清除圖片',
                    child: IconButton.filledTonal(
                      key: ValueKey('clear-$title'),
                      onPressed: onClear,
                      icon: const Icon(Icons.close),
                    ),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: SizedBox(
              width: double.infinity,
              height: 48,
              child: ElevatedButton(
                onPressed: onPick,
                child: Text(buttonText, style: const TextStyle(fontSize: 16)),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildPreviewFrame({required Widget child}) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: AspectRatio(
          aspectRatio: 16 / 9,
          child: Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: Colors.black12,
              border: Border.all(color: Colors.grey),
              borderRadius: BorderRadius.circular(8),
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: child,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPromptSection() {
    final String prompt = _promptController.text.trim();
    final bool hasPrompt = prompt.isNotEmpty;
    final bool hasReference = _referenceImageBytes != null;
    final bool hasConflict = hasPrompt && hasReference;
    final String helperText = hasConflict
        ? '目前同時有 prompt 和參考圖，請保留其中一種。'
        : '請使用文字修圖或參考圖修圖其中一種。';

    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '修圖需求',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),
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
              decoration: const InputDecoration(
                hintText: '例如：幫我調亮一點、暖一點、更鮮豔',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              helperText,
              style: TextStyle(
                color: hasConflict ? Colors.red : Colors.black54,
                fontSize: 14,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildResultDetails() {
    if (_resultExplanation == null && _resultParameters == null) {
      return const SizedBox.shrink();
    }

    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 16),
            const Text(
              '本次調整',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            if (_sessionId != null) ...[
              const SizedBox(height: 8),
              Text('Session：$_sessionId'),
            ],
            if (_latestEditId != null) ...[
              const SizedBox(height: 8),
              Text('目前版本：$_latestEditId'),
            ],
            if (_resolvedIntent != null) ...[
              const SizedBox(height: 8),
              Text('解析結果：$_resolvedIntent'),
            ],
            if (_editMode != null) ...[
              const SizedBox(height: 8),
              Text('修圖模式：$_editMode'),
            ],
            if (_parserSource != null) ...[
              const SizedBox(height: 8),
              Text('解析來源：${formatParserSourceLabel(_parserSource)}'),
            ],
            if (_resultExplanation != null) ...[
              const SizedBox(height: 8),
              Text(_resultExplanation!),
            ],
            if (_resultParameters != null) ...[
              const SizedBox(height: 8),
              SelectableText(_formatParameters(_resultParameters!)),
            ],
          ],
        ),
      ),
    );
  }

  String _formatParameters(Map<String, dynamic> parameters) {
    return parameters.entries
        .map((entry) => '${entry.key}: ${entry.value}')
        .join('\n');
  }

  Widget _buildHistorySection() {
    if (_editHistory.isEmpty) {
      return const SizedBox.shrink();
    }

    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 24),
            const Text(
              '修圖歷史',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),
            for (final entry in _editHistory.asMap().entries)
              _buildHistoryTile(index: entry.key, item: entry.value),
          ],
        ),
      ),
    );
  }

  Widget _buildHistoryTile({
    required int index,
    required EditHistoryItem item,
  }) {
    final String titlePrompt = item.prompt.isEmpty ? '參考圖修圖' : item.prompt;
    final String subtitleParts = [
      if (item.resolvedIntent != null) 'intent=${item.resolvedIntent}',
      if (item.parserSource != null)
        'source=${formatParserSourceLabel(item.parserSource)}',
      if (item.parentEditId != null) 'parent=${item.parentEditId}',
    ].join(' / ');

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        contentPadding: const EdgeInsets.all(12),
        shape: RoundedRectangleBorder(
          side: const BorderSide(color: Colors.black12),
          borderRadius: BorderRadius.circular(8),
        ),
        leading: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: Image.network(
            item.resultUrl,
            width: 64,
            height: 64,
            fit: BoxFit.cover,
            errorBuilder: (context, error, stackTrace) {
              return Container(
                width: 64,
                height: 64,
                color: Colors.black12,
                alignment: Alignment.center,
                child: const Icon(Icons.image_not_supported_outlined),
              );
            },
          ),
        ),
        title: Text(
          '${index + 1}. $titlePrompt',
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: subtitleParts.isEmpty
            ? SelectableText(item.editId)
            : SelectableText('$subtitleParts\nedit=${item.editId}'),
        onTap: () {
          setState(() {
            _resultImageUrl = item.resultUrl;
            _resolvedIntent = item.resolvedIntent;
            _parserSource = item.parserSource;
            _resultParameters = item.parameters;
            _latestEditId = item.editId;
          });
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool hasPrompt = _promptController.text.trim().isNotEmpty;
    final bool hasReference = _referenceImageBytes != null;
    final bool hasOneEditMode = hasPrompt != hasReference;
    final bool canContinueFromLatest =
        _sessionId != null && _latestEditId != null && hasPrompt && !hasReference;
    final bool canStartEdit =
        (_originalImageBytes != null || canContinueFromLatest) &&
        hasOneEditMode &&
        !_isProcessing;

    return Scaffold(
      appBar: AppBar(title: const Text('AI Photo Editor'), centerTitle: true),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _buildImageSection(
              title: '原始圖片',
              imageBytes: _originalImageBytes,
              onPick: () => _pickImage(isOriginal: true),
              onClear: () => _clearImage(isOriginal: true),
              buttonText: '選擇原始圖片',
            ),
            const SizedBox(height: 24),
            _buildImageSection(
              title: '參考圖片',
              imageBytes: _referenceImageBytes,
              onPick: () => _pickImage(isOriginal: false),
              onClear: () => _clearImage(isOriginal: false),
              buttonText: '選擇參考圖片',
            ),
            const SizedBox(height: 24),
            _buildPromptSection(),
            const SizedBox(height: 24),
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 720),
                child: SizedBox(
                  width: double.infinity,
                  height: 52,
                  child: ElevatedButton(
                    onPressed: canStartEdit ? _startEdit : null,
                    child: const Text('開始修圖', style: TextStyle(fontSize: 18)),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 24),
            if (_isProcessing) ...[
              const Center(child: CircularProgressIndicator()),
              const SizedBox(height: 12),
              const Center(
                child: Text('修圖中，請稍候...', style: TextStyle(fontSize: 16)),
              ),
              const SizedBox(height: 24),
            ],
            if (_errorMessage != null) ...[
              Text(
                _errorMessage!,
                style: const TextStyle(fontSize: 16, color: Colors.red),
              ),
              const SizedBox(height: 24),
            ],
            if (_resultImageUrl != null) ...[
              const Text(
                '結果圖片',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              _buildPreviewFrame(
                child: Image.network(
                  _resultImageUrl!,
                  fit: BoxFit.contain,
                  errorBuilder: (context, error, stackTrace) {
                    return const Center(
                      child: Text('結果圖片載入失敗', style: TextStyle(fontSize: 16)),
                    );
                  },
                ),
              ),
              _buildResultDetails(),
            ],
            _buildHistorySection(),
          ],
        ),
      ),
    );
  }
}
