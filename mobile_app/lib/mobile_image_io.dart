import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:image_picker/image_picker.dart';

class MobileImageInfo {
  const MobileImageInfo(this.bytes, this.width, this.height, this.extension);
  final Uint8List bytes;
  final int width;
  final int height;
  final String extension;
}

class MobileImageException implements Exception {
  const MobileImageException(this.code);
  final String code;
}

/// Validate real bytes, not an untrusted extension. No resize/re-encoding.
class MobileImageIO {
  static const maxBytes = 40 * 1024 * 1024;
  static const maxPixels = 24 * 1000 * 1000;

  static Future<MobileImageInfo> read(XFile file) async {
    if (await file.length() > maxBytes) {
      throw const MobileImageException('image_too_large');
    }
    return inspect(await file.readAsBytes());
  }

  static Future<MobileImageInfo> inspect(Uint8List bytes) async {
    if (bytes.length > maxBytes) {
      throw const MobileImageException('image_too_large');
    }
    final png =
        bytes.length >= 8 &&
        bytes[0] == 137 &&
        bytes[1] == 80 &&
        bytes[2] == 78 &&
        bytes[3] == 71 &&
        bytes[4] == 13 &&
        bytes[5] == 10 &&
        bytes[6] == 26 &&
        bytes[7] == 10;
    final jpeg =
        bytes.length >= 3 &&
        bytes[0] == 255 &&
        bytes[1] == 216 &&
        bytes[2] == 255;
    if (!png && !jpeg) throw const MobileImageException('image_format');
    ui.ImmutableBuffer? buffer;
    ui.ImageDescriptor? descriptor;
    try {
      buffer = await ui.ImmutableBuffer.fromUint8List(bytes);
      descriptor = await ui.ImageDescriptor.encoded(buffer);
      if (descriptor.width < 1 ||
          descriptor.height < 1 ||
          descriptor.width * descriptor.height > maxPixels) {
        throw const MobileImageException('image_dimensions');
      }
      return MobileImageInfo(
        bytes,
        descriptor.width,
        descriptor.height,
        png ? 'png' : 'jpg',
      );
    } on MobileImageException {
      rethrow;
    } catch (_) {
      throw const MobileImageException('image_invalid');
    } finally {
      descriptor?.dispose();
      buffer?.dispose();
    }
  }
}
