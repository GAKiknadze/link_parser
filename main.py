import asyncio
import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin, urlparse
import time
import signal
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Set

@dataclass
class LinkInfo:
    text: str
    url: str
    status_code: Optional[int] = None
    is_valid: bool = False
    error: Optional[str] = None
    domain: str = ""
    response_time: float = 0.0

def setup_selenium_driver():
    """Настройка Selenium драйвера для максимальной скорости"""
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Отключаем загрузку изображений и медиа для ускорения
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.video": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.geolocation": 2
    }
    options.add_experimental_option("prefs", prefs)
    
    # JavaScript для ускорения загрузки
    options.add_argument('--disable-javascript')
    
    driver = webdriver.Chrome(options=options)
    
    # Маскировка Selenium
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = {runtime: {}, app: {}};
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        '''
    })
    
    return driver

def get_links_with_selenium(url: str) -> List[LinkInfo]:
    """Получение ссылок с использованием Selenium с оптимизацией скорости"""
    driver = setup_selenium_driver()
    links = []
    seen_urls: Set[str] = set()
    
    try:
        print(f"🌐 Загрузка страницы: {url}")
        start_time = time.time()
        
        # Устанавливаем короткий таймаут загрузки страницы
        driver.set_page_load_timeout(15)
        driver.get(url)
        
        # Ждем минимальной загрузки DOM
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        
        # Прокручиваем страницу для загрузки динамического контента
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        
        # Собираем ссылки
        a_tags = driver.find_elements(By.TAG_NAME, 'a')
        base_url = driver.current_url
        domain = urlparse(base_url).netloc
        
        for a in a_tags:
            try:
                href = a.get_attribute('href')
                text = a.text.strip()
                
                # Фильтрация нерелевантных ссылок
                if not href or href.startswith(('javascript:', 'mailto:', 'tel:', 'file:', 'data:', '#', 'about:')):
                    continue
                
                # Нормализация URL
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                normalized_url = parsed._replace(
                    fragment="",
                    query=""
                ).geturl()
                
                # Проверка на уникальность
                if normalized_url in seen_urls or len(normalized_url) > 2048:
                    continue
                seen_urls.add(normalized_url)
                
                # Ограничиваем текст для экономии памяти
                display_text = text[:100] + "..." if len(text) > 100 else text
                
                links.append(LinkInfo(
                    text=display_text if display_text else normalized_url[:50],
                    url=normalized_url,
                    domain=parsed.netloc or domain
                ))
            except:
                continue
        
        elapsed = time.time() - start_time
        print(f"✅ Найдено {len(links)} уникальных ссылок за {elapsed:.2f} сек")
        
    except Exception as e:
        print(f"❌ Ошибка при получении ссылок: {str(e)}")
    finally:
        driver.quit()
    
    # Ограничиваем количество для скорости проверки
    return links[:200]

async def check_url_status(session: aiohttp.ClientSession, url: str, timeout: float = 2.0) -> Dict:
    """Максимально быстрая проверка статуса с использованием HEAD запросов"""
    start_time = time.time()
    result = {
        'url': url,
        'status_code': None,
        'is_valid': False,
        'error': None,
        'response_time': 0.0
    }
    
    try:
        # Сначала пробуем HEAD запрос
        try:
            async with session.head(
                url,
                allow_redirects=True,
                timeout=ClientTimeout(total=timeout, sock_read=1.0),
                headers={
                    'Connection': 'close',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache',
                    'User-Agent': 'Mozilla/5.0 (compatible; LinkChecker/1.0; +https://example.com/bot)'
                },
                skip_auto_headers=['Accept-Encoding']
            ) as response:
                result['status_code'] = response.status
                result['is_valid'] = 200 <= response.status < 400
                result['response_time'] = time.time() - start_time
                return result
                
        except (aiohttp.ClientResponseError, aiohttp.ClientError) as e:
            # Если HEAD не поддерживается (405), пробуем GET с ограничением
            if hasattr(e, 'status') and e.status == 405:
                pass
            else:
                raise
        
        # Fallback к GET с ограничением размера
        async with session.get(
            url,
            allow_redirects=True,
            timeout=ClientTimeout(total=timeout, sock_read=1.5),
            headers={
                'Connection': 'close',
                'Accept': 'text/html,application/xhtml+xml;q=0.9',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'no-cache',
                'Range': 'bytes=0-1023'  # Запрашиваем только первые 1KB
            }
        ) as response:
            # Принудительно не читаем тело ответа
            result['status_code'] = response.status
            result['is_valid'] = 200 <= response.status < 400
            result['response_time'] = time.time() - start_time
            return result
            
    except asyncio.TimeoutError:
        result['error'] = 'Timeout'
    except aiohttp.ClientResponseError as e:
        result['status_code'] = e.status
        result['is_valid'] = 200 <= e.status < 400
        result['error'] = str(e)
    except aiohttp.ClientError as e:
        result['error'] = f'Network: {str(e)}'
    except Exception as e:
        result['error'] = f'Unknown: {str(e)}'
    
    result['response_time'] = time.time() - start_time
    return result

async def check_links_ultra_fast(links: List[LinkInfo]) -> List[LinkInfo]:
    """Ультра-быстрая проверка всех ссылок с максимальной оптимизацией"""
    # Группируем по доменам для балансировки
    domain_groups = {}
    for idx, link in enumerate(links):
        if link.domain not in domain_groups:
            domain_groups[link.domain] = []
        domain_groups[link.domain].append((idx, link))
    
    connector = TCPConnector(
        limit=100,           # Общий лимит соединений
        limit_per_host=15,   # Лимит на домен
        enable_cleanup_closed=True,
        force_close=True,
        ssl=False            # Отключаем SSL для скорости
    )
    
    timeout = ClientTimeout(total=3.0, connect=1.0)
    
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Connection': 'close'
        },
        trust_env=False
    ) as session:
        
        async def process_domain(domain, items):
            results = []
            # Обрабатываем по 15 ссылок за раз для каждого домена
            for i in range(0, len(items), 15):
                batch = items[i:i+15]
                tasks = [check_url_status(session, link.url) for _, link in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for (idx, link), res in zip(batch, batch_results):
                    if isinstance(res, Exception):
                        result = {'error': str(res)}
                    else:
                        result = res
                    
                    link.status_code = result.get('status_code')
                    link.is_valid = result.get('is_valid', False)
                    link.error = result.get('error')
                    link.response_time = result.get('response_time', 0.0)
                    results.append(link)
                
                # Короткая пауза между пакетами для одного домена
                await asyncio.sleep(0.01)
            return results
        
        # Запускаем проверку для всех доменов параллельно
        domain_tasks = [process_domain(domain, items) for domain, items in domain_groups.items()]
        domain_results = await asyncio.gather(*domain_tasks)
        
        # Собираем результаты в исходном порядке
        all_results = [None] * len(links)
        for domain_result in domain_results:
            for link in domain_result:
                # Находим исходный индекс (грубый метод, но быстрый)
                for i, orig_link in enumerate(links):
                    if orig_link.url == link.url and all_results[i] is None:
                        all_results[i] = link
                        break
        
        return [link for link in all_results if link is not None]

def report_results(links: List[LinkInfo]):
    """Вывод результатов в оптимизированном формате"""
    valid = [l for l in links if l.is_valid]
    invalid = [l for l in links if not l.is_valid]
    
    print(f"\n{'='*60}")
    print(f"✅ ВАЛИДНЫЕ ССЫЛКИ (200-399): {len(valid)}")
    print(f"{'-'*60}")
    for i, link in enumerate(valid[:10], 1):  # Показываем только первые 10
        print(f"{i}. {link.text[:50]}")
        print(f"   → {link.url[:70]}")
        print(f"   📊 {link.status_code} | ⏱️ {link.response_time:.3f}с\n")
    
    if len(valid) > 10:
        print(f"... и еще {len(valid) - 10} валидных ссылок")
    
    print(f"\n{'='*60}")
    print(f"❌ НЕВАЛИДНЫЕ ССЫЛКИ: {len(invalid)}")
    print(f"{'-'*60}")
    for i, link in enumerate(invalid[:10], 1):  # Показываем только первые 10
        status = link.status_code if link.status_code else "ERR"
        error = f" | {link.error}" if link.error else ""
        print(f"{i}. {link.text[:50]}")
        print(f"   → {link.url[:70]}")
        print(f"   📊 {status}{error}")
        print(f"   ⏱️ {link.response_time:.3f}с\n")
    
    if len(invalid) > 10:
        print(f"... и еще {len(invalid) - 10} невалидных ссылок")
    
    # Сохранение полных результатов
    with open('valid_links.txt', 'w', encoding='utf-8') as f:
        for link in valid:
            f.write(f"{link.text} | {link.url} | {link.status_code} | {link.response_time:.3f}\n")
    
    with open('invalid_links.txt', 'w', encoding='utf-8') as f:
        for link in invalid:
            status = link.status_code or "ERR"
            error = link.error or ""
            f.write(f"{link.text} | {link.url} | {status} | {error} | {link.response_time:.3f}\n")
    
    print(f"\n✅ Полные результаты сохранены в:")
    print(f"   - valid_links.txt ({len(valid)} ссылок)")
    print(f"   - invalid_links.txt ({len(invalid)} ссылок)")

async def main(url: str):
    """Основной процесс с максимальной оптимизацией скорости"""
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    
    print("="*60)
    print("🚀 СВЕРХБЫСТРАЯ ПРОВЕРКА ССЫЛОК С ИСПОЛЬЗОВАНИЕМ SELENIUM")
    print("="*60)
    print(f"🎯 Целевой URL: {url}")
    print(f"⚡ Стратегия: Selenium для сбора + HEAD запросы для проверки")
    print("-"*60)
    
    total_start = time.time()
    
    # Этап 1: Получение ссылок через Selenium
    links = get_links_with_selenium(url)
    if not links:
        print("❌ Не удалось получить ссылки с страницы")
        return
    
    # Этап 2: Сверхбыстрая проверка статусов
    print(f"\n⚡ Запуск ультра-быстрой проверки {len(links)} ссылок...")
    check_start = time.time()
    
    checked_links = await check_links_ultra_fast(links)
    
    check_time = time.time() - check_start
    total_time = time.time() - total_start
    
    # Этап 3: Отчет
    print(f"\n{'='*60}")
    print(f"⏱️  Время проверки: {check_time:.2f} сек")
    print(f"⚡ Скорость: {len(links)/check_time:.1f} ссылок/сек")
    print(f"🏁 Общее время: {total_time:.2f} сек")
    
    report_results(checked_links)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Сверхбыстрая проверка ссылок с использованием Selenium')
    parser.add_argument('url', type=str, help='URL для анализа')
    args = parser.parse_args()
    
    asyncio.run(main(args.url))