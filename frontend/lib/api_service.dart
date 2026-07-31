import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'edit_models.dart';

abstract class EditorApi {
  String buildImageUrl(String path);

  Future<EditHistoryItem> submitEdit({
    required Uint8List? originalBytes,
    required Uint8List? referenceBytes,
    required String prompt,
    required String clientRequestId,
    String? sessionId,
    String? parentEditId,
  });

  Future<EditSession> fetchSession(String sessionId);

  Future<StyleCatalog> fetchStyleCatalog();

  Future<ManualSchema> fetchManualSchema();

  Future<EditContractSchema> fetchEditContractSchema();

  Future<ManualEditResponse> previewManual({
    required String sessionId,
    required String sourceEditId,
    required Map<String, double> parameterOverrides,
    required String clientRequestId,
    http.Client? requestClient,
  });

  Future<ManualEditResponse> commitManual({
    required String sessionId,
    required String sourceEditId,
    required Map<String, double> parameterOverrides,
    required String clientRequestId,
  });

  Future<PhotoGitPlan> planPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
  });

  Future<PhotoGitPreview> previewPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
    required String planHash,
  });

  Future<EditHistoryItem> commitPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
    required String planHash,
    required String clientRequestId,
  });
}

class ApiException implements Exception {
  const ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
    this.details = const <String, dynamic>{},
  });

  final int statusCode;
  final String? code;
  final String message;
  final Map<String, dynamic> details;

  @override
  String toString() => message;
}

class ApiService implements EditorApi {
  ApiService({String? baseUrl, http.Client? client})
    : baseUrl = (baseUrl ?? environmentBaseUrl).replaceFirst(RegExp(r'/$'), ''),
      _client = client ?? http.Client(),
      _ownsClient = client == null;

  static const String environmentBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );

  final String baseUrl;
  final http.Client _client;
  final bool _ownsClient;

  @override
  Future<EditHistoryItem> submitEdit({
    required Uint8List? originalBytes,
    required Uint8List? referenceBytes,
    required String prompt,
    required String clientRequestId,
    String? sessionId,
    String? parentEditId,
  }) async {
    final request = createEditRequest(
      originalBytes: originalBytes,
      referenceBytes: referenceBytes,
      prompt: prompt,
      clientRequestId: clientRequestId,
      sessionId: sessionId,
      parentEditId: parentEditId,
    );
    try {
      final streamedResponse = await _client.send(request);
      final response = await http.Response.fromStream(streamedResponse);
      final data = _decodeResponse(response);
      return EditHistoryItem.fromJson(data, buildImageUrl: buildImageUrl);
    } on ApiException {
      rethrow;
    } catch (error) {
      throw ApiException(
        statusCode: 0,
        code: 'network_error',
        message: '無法連線到修圖後端：$error',
      );
    }
  }

  http.MultipartRequest createEditRequest({
    required Uint8List? originalBytes,
    required Uint8List? referenceBytes,
    required String prompt,
    required String clientRequestId,
    String? sessionId,
    String? parentEditId,
  }) {
    final request = http.MultipartRequest('POST', Uri.parse('$baseUrl/edit'))
      ..fields['prompt'] = prompt
      ..fields['client_request_id'] = clientRequestId;

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

  @override
  Future<EditSession> fetchSession(String sessionId) async {
    final response = await _get('/edit/sessions/$sessionId');
    return EditSession.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<StyleCatalog> fetchStyleCatalog() async {
    final response = await _get('/edit/styles');
    return StyleCatalog.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<ManualSchema> fetchManualSchema() async {
    final response = await _get('/edit/manual/schema');
    return ManualSchema.fromJson(response);
  }

  @override
  Future<EditContractSchema> fetchEditContractSchema() async {
    final response = await _get('/edit/contracts/schema');
    return EditContractSchema.fromJson(response);
  }

  @override
  Future<ManualEditResponse> previewManual({
    required String sessionId,
    required String sourceEditId,
    required Map<String, double> parameterOverrides,
    required String clientRequestId,
    http.Client? requestClient,
  }) async {
    final response = await _postJson('/edit/manual/preview', {
      'session_id': sessionId,
      'source_edit_id': sourceEditId,
      'parameter_overrides': parameterOverrides,
      'client_request_id': clientRequestId,
    }, requestClient: requestClient);
    return ManualEditResponse.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<ManualEditResponse> commitManual({
    required String sessionId,
    required String sourceEditId,
    required Map<String, double> parameterOverrides,
    required String clientRequestId,
  }) async {
    final response = await _postJson('/edit/manual/commit', {
      'session_id': sessionId,
      'source_edit_id': sourceEditId,
      'parameter_overrides': parameterOverrides,
      'client_request_id': clientRequestId,
    });
    return ManualEditResponse.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<PhotoGitPlan> planPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
  }) async {
    final response = await _postJson(
      '/edit/photo-git/plan',
      request.toJson(sessionId),
    );
    return PhotoGitPlan.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<PhotoGitPreview> previewPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
    required String planHash,
  }) async {
    final response = await _postJson('/edit/photo-git/preview', {
      ...request.toJson(sessionId),
      'plan_hash': planHash,
    });
    return PhotoGitPreview.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  Future<EditHistoryItem> commitPhotoGit({
    required String sessionId,
    required PhotoGitRequest request,
    required String planHash,
    required String clientRequestId,
  }) async {
    final response = await _postJson('/edit/photo-git/commit', {
      ...request.toJson(sessionId),
      'plan_hash': planHash,
      'client_request_id': clientRequestId,
    });
    return EditHistoryItem.fromJson(response, buildImageUrl: buildImageUrl);
  }

  @override
  String buildImageUrl(String path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    final separator = path.startsWith('/') ? '' : '/';
    return '$baseUrl$separator$path';
  }

  Future<Map<String, dynamic>> _get(String path) async {
    try {
      final response = await _client.get(Uri.parse('$baseUrl$path'));
      return _decodeResponse(response);
    } on ApiException {
      rethrow;
    } catch (error) {
      throw ApiException(
        statusCode: 0,
        code: 'network_error',
        message: '無法連線到修圖後端：$error',
      );
    }
  }

  Future<Map<String, dynamic>> _postJson(
    String path,
    Map<String, dynamic> body, {
    http.Client? requestClient,
  }) async {
    try {
      final response = await (requestClient ?? _client).post(
        Uri.parse('$baseUrl$path'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode(body),
      );
      return _decodeResponse(response);
    } on ApiException {
      rethrow;
    } catch (error) {
      throw ApiException(
        statusCode: 0,
        code: 'network_error',
        message: '無法連線到修圖後端：$error',
      );
    }
  }

  Map<String, dynamic> _decodeResponse(http.Response response) {
    dynamic decoded;
    try {
      decoded = jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      decoded = null;
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final detail = decoded is Map ? decoded['detail'] : null;
      final detailMap = detail is Map
          ? Map<String, dynamic>.from(detail)
          : const <String, dynamic>{};
      final message =
          detailMap['message']?.toString() ??
          (detail is String ? detail : null) ??
          '後端請求失敗（HTTP ${response.statusCode}）';
      throw ApiException(
        statusCode: response.statusCode,
        code: detailMap['code']?.toString(),
        message: message,
        details: detailMap,
      );
    }

    if (decoded is! Map) {
      throw const ApiException(
        statusCode: 200,
        code: 'invalid_response',
        message: '後端回傳了無法辨識的資料格式。',
      );
    }
    return Map<String, dynamic>.from(decoded);
  }

  void close() {
    if (_ownsClient) {
      _client.close();
    }
  }
}
