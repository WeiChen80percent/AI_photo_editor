import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

class ApiService {
  static const String baseUrl = 'http://127.0.0.1:8000';

  static Future<Map<String, dynamic>> uploadImages({
    required Uint8List originalBytes,
    required Uint8List referenceBytes,
  }) async {
    final uri = Uri.parse('$baseUrl/edit');

    final request = http.MultipartRequest('POST', uri)
      ..files.add(
        http.MultipartFile.fromBytes(
          'original_image',
          originalBytes,
          filename: 'original.png',
        ),
      )
      ..files.add(
        http.MultipartFile.fromBytes(
          'reference_image',
          referenceBytes,
          filename: 'reference.png',
        ),
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

  static String buildImageUrl(String path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    return '$baseUrl$path';
  }
}