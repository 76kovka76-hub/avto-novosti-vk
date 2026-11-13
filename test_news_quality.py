"""Text-only editorial gate tests, all offline."""
import unittest
from news_quality import is_self_contained_news, mentions_missing_media, source_url


class NewsQualityTests(unittest.TestCase):
    def test_good_news_stands_alone(self):
        for headline in (
            "В Ярославле перекроют дорогу на улице Ленина из-за ремонта",
            "В Краснодаре открыли новый мост через реку Кубань после ремонта",
            "София Молчанова из Екатеринбурга завоевала золото чемпионата России по плаванию",
            "В Новосибирске запустят автобус по обновлённому маршруту с понедельника",
            "АвтоВАЗ запустит производство новой модели Lada в Тольятти в 2027 году",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(is_self_contained_news(headline))

    def test_media_dependent_copy_is_rejected(self):
        for headline in (
            "В Краснодаре появилось видео аварии на центральной улице города",
            "В Ярославле показали фото обновлённого парка после ремонта",
            "В Новосибирске появились кадры с места дорожного происшествия",
            "В Екатеринбурге открыли парк, смотрите как он выглядит",
            "На автомобильной выставке показали снимки новой модели машины",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(mentions_missing_media(headline))
                self.assertFalse(is_self_contained_news(headline))

    def test_unfinished_clickbait_is_rejected(self):
        for headline in (
            "Стало известно, какие улицы отремонтируют в Краснодаре",
            "В Ярославле рассказали, что произойдёт с тарифами на воду",
            "Почему в Екатеринбурге выросли цены на новые автомобили",
            "В Новосибирске произошла авария — подробности читайте в материале",
            "Автомобильная компания представила новинку — вот что известно",
            "В Краснодаре откроют новую улицу для движения, но",
            "В Ярославле открыли ещё одну дорогу...",
        ):
            with self.subTest(headline=headline):
                self.assertFalse(is_self_contained_news(headline))

    def test_valid_source_link(self):
        self.assertEqual(source_url("https://example.com/news/123"), "https://example.com/news/123")
        for url in ("javascript:alert(1)", "file:///etc/passwd", "", "https://example.com/a b"):
            self.assertIsNone(source_url(url))


if __name__ == "__main__":
    unittest.main()
