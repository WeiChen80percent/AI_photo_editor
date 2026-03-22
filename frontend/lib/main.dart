import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'api_service.dart';

void main() {
  runApp(const MyApp());
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

  Uint8List? _originalImageBytes;
  Uint8List? _referenceImageBytes;

  String? _resultImageUrl;

  bool _isProcessing = false;
  String? _errorMessage;

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
      } else {
        _referenceImageBytes = bytes;
      }
      _errorMessage = null;
    });
  }

  Future<void> _startEdit() async {
    if (_originalImageBytes == null || _referenceImageBytes == null) {
      return;
    }

    setState(() {
      _isProcessing = true;
      _resultImageUrl = null;
      _errorMessage = null;
    });

    try {
      final result = await ApiService.uploadImages(
        originalBytes: _originalImageBytes!,
        referenceBytes: _referenceImageBytes!,
      );

      final String resultUrl = ApiService.buildImageUrl(
        result['result_url'] as String,
      );

      setState(() {
        _resultImageUrl = resultUrl;
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
    required String buttonText,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 12),
        Container(
          width: double.infinity,
          height: 220,
          decoration: BoxDecoration(
            color: Colors.black12,
            border: Border.all(color: Colors.grey),
            borderRadius: BorderRadius.circular(16),
          ),
          child: imageBytes == null
              ? const Center(
                  child: Text(
                    '尚未選擇圖片',
                    style: TextStyle(fontSize: 18),
                  ),
                )
              : ClipRRect(
                  borderRadius: BorderRadius.circular(16),
                  child: Image.memory(
                    imageBytes,
                    fit: BoxFit.contain,
                  ),
                ),
        ),
        const SizedBox(height: 12),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: ElevatedButton(
            onPressed: onPick,
            child: Text(
              buttonText,
              style: const TextStyle(fontSize: 16),
            ),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool canStartEdit =
        _originalImageBytes != null &&
        _referenceImageBytes != null &&
        !_isProcessing;

    return Scaffold(
      appBar: AppBar(
        title: const Text('AI Photo Editor'),
        centerTitle: true,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _buildImageSection(
              title: '原始圖片',
              imageBytes: _originalImageBytes,
              onPick: () => _pickImage(isOriginal: true),
              buttonText: '選擇原始圖片',
            ),
            const SizedBox(height: 24),
            _buildImageSection(
              title: '參考圖片',
              imageBytes: _referenceImageBytes,
              onPick: () => _pickImage(isOriginal: false),
              buttonText: '選擇參考圖片',
            ),
            const SizedBox(height: 24),
            SizedBox(
              height: 52,
              child: ElevatedButton(
                onPressed: canStartEdit ? _startEdit : null,
                child: const Text(
                  '開始修圖',
                  style: TextStyle(fontSize: 18),
                ),
              ),
            ),
            const SizedBox(height: 24),
            if (_isProcessing) ...[
              const Center(child: CircularProgressIndicator()),
              const SizedBox(height: 12),
              const Center(
                child: Text(
                  '修圖中，請稍候...',
                  style: TextStyle(fontSize: 16),
                ),
              ),
              const SizedBox(height: 24),
            ],
            if (_errorMessage != null) ...[
              Text(
                _errorMessage!,
                style: const TextStyle(
                  fontSize: 16,
                  color: Colors.red,
                ),
              ),
              const SizedBox(height: 24),
            ],
            if (_resultImageUrl != null) ...[
              const Text(
                '結果圖片',
                style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 12),
              Container(
                width: double.infinity,
                height: 220,
                decoration: BoxDecoration(
                  color: Colors.black12,
                  border: Border.all(color: Colors.grey),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(16),
                  child: Image.network(
                    _resultImageUrl!,
                    fit: BoxFit.contain,
                    errorBuilder: (context, error, stackTrace) {
                      return const Center(
                        child: Text(
                          '結果圖片載入失敗',
                          style: TextStyle(fontSize: 16),
                        ), 
                      );
                    },
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}