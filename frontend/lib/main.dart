import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

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

  XFile? _originalImageFile;
  XFile? _referenceImageFile;

  Uint8List? _originalImageBytes;
  Uint8List? _referenceImageBytes;
  Uint8List? _resultImageBytes;

  bool _isProcessing = false;

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
        _originalImageFile = pickedFile;
        _originalImageBytes = bytes;
      } else {
        _referenceImageFile = pickedFile;
        _referenceImageBytes = bytes;
      }
    });
  }

  Future<void> _startMockEdit() async {
    if (_originalImageBytes == null || _referenceImageBytes == null) {
      return;
    }

    setState(() {
      _isProcessing = true;
      _resultImageBytes = null;
    });

    await Future.delayed(const Duration(seconds: 2));

    setState(() {
      // 目前先用原圖當作「假結果」
      // 之後接後端時，再改成真正 API 回傳的圖片
      _resultImageBytes = _originalImageBytes;
      _isProcessing = false;
    });
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
                onPressed: canStartEdit ? _startMockEdit : null,
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
            if (_resultImageBytes != null) ...[
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
                  border: Border.all(color: Colors.grey),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(16),
                  child: Image.memory(
                    _resultImageBytes!,
                    fit: BoxFit.contain,
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