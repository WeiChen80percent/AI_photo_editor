import 'mobile_image_io.dart';

class GalleryExport {
  static bool get supported => false;
  static Future<void> save(MobileImageInfo image, String name) async {
    throw UnsupportedError('Gallery export requires Android or iOS.');
  }
}
