import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from learning_app.knowledge_pipeline import downloader as downloader


class ScopeTests(unittest.TestCase):
    def test_http_does_not_use_user_netrc_or_proxy_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            netrc=Path(folder)/'netrc'
            netrc.write_text('machine www.youtube.com login unrelated-user password unrelated-password\n')
            with patch.dict(os.environ, {'NETRC':str(netrc),'HTTPS_PROXY':'http://not-part-of-this-task.invalid:3128'}):
                source=downloader.Source()
                request=downloader.requests.Request('GET','https://www.youtube.com/watch?v=-gqYldhjZVk')
                prepared=source.session.prepare_request(request)
                self.assertNotIn('Authorization',prepared.headers)
                settings=source.session.merge_environment_settings(prepared.url,{},None,None,None)
                self.assertEqual(dict(settings['proxies']),{})

    def test_external_redirect_is_rejected_before_fetching_any_other_url(self):
        source=downloader.Source()
        response=Mock(status_code=302)
        source.session.get=Mock(return_value=response)
        source.session.post=Mock()
        with self.assertRaises(downloader.DownloadError):
            source.get_panel({'id':'-gqYldhjZVk','title':'Sample'})
        source.session.get.assert_called_once()
        self.assertIs(source.session.get.call_args.kwargs['allow_redirects'],False)
        source.session.post.assert_not_called()

    def test_inventory_cannot_load_other_extractors_credentials_or_media(self):
        with patch.object(downloader,'YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.extract_info.return_value={
                'channel_id':downloader.CHANNEL_ID,'entries':[{'id':'-gqYldhjZVk','title':'Sample'}]}
            self.assertEqual(len(downloader.Source().list_videos()),1)
        options=factory.call_args.args[0]
        self.assertTrue(options['extract_flat'])
        self.assertTrue(options['skip_download'])
        self.assertFalse(options['cachedir'])
        self.assertFalse(options['usenetrc'])
        self.assertEqual(options['proxy'],'')
        self.assertIsNone(options['cookiesfrombrowser'])
        self.assertIsNone(options['cookiefile'])
        self.assertEqual(options['allowed_extractors'],['^youtube:tab$'])
        self.assertEqual(options['postprocessors'],[])
        self.assertEqual(os.environ['YTDLP_NO_PLUGINS'],'1')


if __name__=='__main__':
    unittest.main()
