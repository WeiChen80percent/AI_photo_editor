import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

class ApiService {
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );

  static Future<Map<String, dynamic>> uploadImages({
    required Uint8List? originalBytes,
    required Uint8List? referenceBytes,
    required String prompt,
    String? sessionId,
    String? parentEditId,
  }) async {
    final request = buildEditRequest(
      originalBytes: originalBytes,
      referenceBytes: referenceBytes,
      prompt: prompt,
      sessionId: sessionId,
      parentEditId: parentEditId,
    );
    final streamedResponse = await request.send();
    final response = await http.Response.fromStream(streamedResponse);

    if (response.statusCode != 200) {
      throw Exception(
        'Upload failed: ${response.statusCode}, body: ${response.body}',
      );
    }

    final Map<String, dynamic> data = jsonDecode(response.body);
    return data;
  }

  static http.MultipartRequest buildEditRequest({
    required Uint8List? originalBytes,
    required Uint8List? referenceBytes,
    required String prompt,
    String? sessionId,
    String? parentEditId,
  }) {
    final request = http.MultipartRequest('POST', Uri.parse('$baseUrl/edit'))
      ..fields['prompt'] = prompt;

    if (sessionId != null && sessionId.isNotEmpty) {
      request.fields['session_id'] = sessionId;
    }
    if (parentEditId != null && parentEditId.isNotEmpty) {
      request.fields['parent_edit_id'] = parentEditId;
    }
    if (originalBytes != null) {
      request.files.add(
        http.MultipartFile.fromBytes(
          'original_image',
          originalBytes,
          filename: 'original.png',
        ),
      );
    }
    if (referenceBytes != null) {
      request.files.add(
        http.MultipartFile.fromBytes(
          'reference_image',
          referenceBytes,
          filename: 'reference.png',
        ),
      );
    }

    return request;
  }

  static String buildImageUrl(String path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    return '$baseUrl$path';
  }
}
