import 'dart:io';

import 'package:gal/gal.dart';
import 'package:path_provider/path_provider.dart';

import 'mobile_image_io.dart';

class GalleryExport {
  static bool get supported => Platform.isAndroid || Platform.isIOS;

  static Future<void> save(MobileImageInfo image, String name) async {
    if (!supported) {
      throw UnsupportedError('Gallery export requires Android or iOS.');
    }
    if (!await Gal.hasAccess()) await Gal.requestAccess();
    if (!await Gal.hasAccess()) {
      throw const MobileImageException('gallery_denied');
    }
    final temporary = await getTemporaryDirectory();
    final safeName = name.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    final file = File(
      '${temporary.path}/ai_photo_${safeName}_${DateTime.now().microsecondsSinceEpoch}.${image.extension}',
    );
    try {
      await file.writeAsBytes(image.bytes, flush: true);
      await Gal.putImage(file.path);
    } on GalException catch (error) {
      throw MobileImageException(switch (error.type) {
        GalExceptionType.accessDenied => 'gallery_denied',
        GalExceptionType.notEnoughSpace => 'gallery_full',
        _ => 'gallery_failed',
      });
    } finally {
      // Remove only this operation's temporary copy, never the gallery asset.
      try {
        if (await file.exists()) await file.delete();
      } catch (_) {}
    }
  }
}
