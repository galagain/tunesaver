import unittest

from test_webapp import webapp


class BeatportArtworkTests(unittest.TestCase):
    def result(self, release):
        return webapp.normalize_beatport_results({'tracks': [{
            'id': 10726480, 'name': "Buggin'", 'slug': 'buggin',
            'image': {'uri': 'https://geo-media.beatport.com/image_size/1500x250/waveform.png'},
            'images': ['https://geo-media.beatport.com/waveform.png'],
            'release': release,
        }]})[0]

    def test_release_artwork_is_preferred_over_track_waveform(self):
        url = 'https://geo-media.beatport.com/image_size/500x500/cover.jpg'
        result = self.result({'image': {'uri': url}})
        self.assertEqual(result['cover_url'], url)
        self.assertEqual(result['download_url'], 'https://www.beatport.com/track/buggin/10726480')

    def test_release_images_and_dynamic_sizes_are_supported(self):
        result = self.result({'images': [{'dynamic_uri': 'https://geo-media.beatport.com/image_size/{w}x{h}/cover.jpg'}]})
        self.assertEqual(result['cover_url'], 'https://geo-media.beatport.com/image_size/320x320/cover.jpg')

    def test_missing_artwork_does_not_display_waveform_as_cover(self):
        for release in (None, {}, {'image': None, 'images': []}):
            with self.subTest(release=release):
                self.assertEqual(self.result(release)['cover_url'], '')
