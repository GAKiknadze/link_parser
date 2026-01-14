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
from tqdm import tqdm
from tqdm.asyncio import tqdm as async_tqdm

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
    """Получение ВСЕХ ссылок с использованием Selenium с прогресс-баром"""
    driver = setup_selenium_driver()
    links = []
    seen_urls: Set[str] = set()
    
    try:
        print(f"\n{'='*60}")
        print(f"🌐 ЗАГРУЗКА СТРАНИЦЫ: {url}")
        print(f"{'='*60}")
        start_time = time.time()
        
        # Устанавливаем короткий таймаут загрузки страницы
        driver.set_page_load_timeout(20)
        driver.get(url)
        
        # Ждем минимальной загрузки DOM
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        
        # Прокручиваем страницу для загрузки динамического контента
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        
        # Дополнительная прокрутка для SPA
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(0.3)
        
        # Собираем ссылки
        a_tags = driver.find_elements(By.TAG_NAME, 'a')
        base_url = driver.current_url
        domain = urlparse(base_url).netloc
        
        print(f"\n🔗 СБОР ССЫЛОК СО СТРАНИЦЫ...")
        print(f"{'-'*60}")
        
        # Прогресс-бар для сбора ссылок
        for a in tqdm(a_tags, desc="Парсинг ссылок", unit="ссылка", dynamic_ncols=True):
            try:
                href = a.get_attribute('href')
                text = a.text.strip()
                
                # Фильтрация нерелевантных ссылок
                if not href or href.startswith(('javascript:', 'mailto:', 'tel:', 'file:', '#', 'about:', 'data:')):
                    continue
                
                # Нормализация URL
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                normalized_url = parsed._replace(
                    fragment="",
                    query=""
                ).geturl()
                
                # Проверка на уникальность
                if normalized_url in seen_urls or len(normalized_url) > 4096:
                    continue
                seen_urls.add(normalized_url)
                
                # Ограничиваем текст для читаемости
                display_text = text[:100].replace('\n', ' ').replace('\r', ' ') + ("..." if len(text) > 100 else "")
                
                links.append(LinkInfo(
                    text=display_text if display_text else normalized_url[:50],
                    url=normalized_url,
                    domain=parsed.netloc or domain
                ))
            except Exception as e:
                continue  # Пропускаем проблемные элементы
        
        elapsed = time.time() - start_time
        print(f"\n✅ Найдено {len(links)} уникальных ссылок за {elapsed:.2f} сек")
        
    except Exception as e:
        print(f"❌ Ошибка при получении ссылок: {str(e)}")
    finally:
        driver.quit()
    
    return links

async def check_url_status(session: aiohttp.ClientSession, url: str, timeout: float = 3.0) -> Dict:
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
                timeout=ClientTimeout(total=timeout, sock_read=1.5),
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
            timeout=ClientTimeout(total=timeout, sock_read=2.0),
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

async def check_links_ultra_fast(links: List[LinkInfo], max_connections: int = 200) -> List[LinkInfo]:
    """Ультра-быстрая проверка ВСЕХ ссылок с прогресс-баром"""
    print(f"\n{'='*60}")
    print(f"⚡ ПРОВЕРКА СТАТУСОВ {len(links)} ССЫЛОК")
    print(f"{'='*60}")
    print("Стратегия: HEAD запросы → GET с ограничением → таймауты 3с")
    print(f"Параметры: {max_connections} соединений, 20/домен")
    print("-"*60)
    
    # Группируем по доменам для балансировки
    domain_groups = {}
    for idx, link in enumerate(links):
        if link.domain not in domain_groups:
            domain_groups[link.domain] = []
        domain_groups[link.domain].append((idx, link))
    
    connector = TCPConnector(
        limit=max_connections,    # Общий лимит соединений
        limit_per_host=20,        # Лимит на домен
        enable_cleanup_closed=True,
        force_close=True,
        ssl=False                 # Отключаем SSL для скорости
    )
    
    timeout = ClientTimeout(total=5.0, connect=2.0)
    
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
        
        # Общее количество задач для прогресс-бара
        total_tasks = len(links)
        pbar = tqdm(total=total_tasks, desc="Проверка статусов", unit="ссылка", dynamic_ncols=True)
        
        async def process_domain(domain, items):
            results = []
            # Обрабатываем по 20 ссылок за раз для каждого домена
            for i in range(0, len(items), 20):
                batch = items[i:i+20]
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
                    pbar.update(1)  # Обновляем прогресс
                
                # Короткая пауза между пакетами для одного домена
                await asyncio.sleep(0.02)
            return results
        
        # Запускаем проверку для всех доменов параллельно
        domain_tasks = [process_domain(domain, items) for domain, items in domain_groups.items()]
        
        try:
            domain_results = await async_tqdm.gather(*domain_tasks, desc="Домены", unit="домен")
        except asyncio.CancelledError:
            print("\n\n⚠️ Проверка прервана пользователем")
            pbar.close()
            raise
        
        pbar.close()
        
        # Собираем результаты в исходном порядке
        all_results = [None] * len(links)
        for domain_result in domain_results:
            for link in domain_result:
                # Находим исходный индекс
                for i, orig_link in enumerate(links):
                    if orig_link.url == link.url and all_results[i] is None:
                        all_results[i] = link
                        break
        
        return [link for link in all_results if link is not None]

def report_results(links: List[LinkInfo]):
    """Вывод результатов с прогресс-баром и детальной статистикой"""
    valid = [l for l in links if l.is_valid]
    invalid = [l for l in links if not l.is_valid]
    
    print(f"\n{'='*60}")
    print(f"✅ ВАЛИДНЫЕ ССЫЛКИ (200-399): {len(valid)} из {len(links)}")
    print(f"{'-'*60}")
    
    # Показываем топ-20 валидных ссылок
    for i, link in enumerate(valid[:20], 1):
        status_color = "\033[92m" if link.is_valid else "\033[91m"
        time_color = "\033[94m" if link.response_time < 1.0 else "\033[93m" if link.response_time < 2.0 else "\033[91m"
        reset = "\033[0m"
        
        print(f"{i}. {link.text[:70]}")
        print(f"   → {link.url[:80]}")
        print(f"   📊 {status_color}{link.status_code}{reset} | ⏱️ {time_color}{link.response_time:.3f}с{reset}\n")
    
    if len(valid) > 20:
        print(f"... и еще {len(valid) - 20} валидных ссылок")
    
    print(f"\n{'='*60}")
    print(f"❌ НЕВАЛИДНЫЕ ССЫЛКИ: {len(invalid)} из {len(links)}")
    print(f"{'-'*60}")
    
    # Показываем топ-20 невалидных ссылок
    for i, link in enumerate(invalid[:20], 1):
        status = link.status_code if link.status_code else "ERR"
        error = f" | {link.error[:50]}" if link.error else ""
        status_color = "\033[92m" if link.is_valid else "\033[91m"
        time_color = "\033[94m" if link.response_time < 1.0 else "\033[93m" if link.response_time < 2.0 else "\033[91m"
        reset = "\033[0m"
        
        print(f"{i}. {link.text[:70]}")
        print(f"   → {link.url[:80]}")
        print(f"   📊 {status_color}{status}{reset}{error}")
        print(f"   ⏱️ {time_color}{link.response_time:.3f}с{reset}\n")
    
    if len(invalid) > 20:
        print(f"... и еще {len(invalid) - 20} невалидных ссылок")
    
    # Детальная статистика
    print(f"\n{'='*60}")
    print("📊 ДЕТАЛЬНАЯ СТАТИСТИКА")
    print(f"{'='*60}")
    
    # Статистика по статус-кодам
    status_counts = {}
    for link in links:
        status = link.status_code or "ERROR"
        status_counts[status] = status_counts.get(status, 0) + 1
    
    print("Статус-коды:")
    for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(links)) * 100
        print(f"  {status}: {count} ({percentage:.1f}%)")
    
    # Статистика по доменам
    domain_stats = {}
    for link in links:
        domain = link.domain or "unknown"
        if domain not in domain_stats:
            domain_stats[domain] = {'total': 0, 'valid': 0}
        domain_stats[domain]['total'] += 1
        if link.is_valid:
            domain_stats[domain]['valid'] += 1
    
    print(f"\nТоп-5 доменов:")
    top_domains = sorted(domain_stats.items(), key=lambda x: x[1]['total'], reverse=True)[:5]
    for domain, stats in top_domains:
        valid_percent = (stats['valid'] / stats['total']) * 100
        print(f"  {domain}: {stats['total']} ссылок | Валидных: {stats['valid']} ({valid_percent:.1f}%)")
    
    # Сохранение полных результатов
    print(f"\n{'='*60}")
    print("💾 СОХРАНЕНИЕ РЕЗУЛЬТАТОВ")
    print(f"{'='*60}")
    
    with open('valid_links.txt', 'w', encoding='utf-8') as f:
        for link in tqdm(valid, desc="Сохранение валидных", unit="ссылка"):
            f.write(f"{link.text} | {link.url} | {link.status_code} | {link.response_time:.3f}\n")
    
    with open('invalid_links.txt', 'w', encoding='utf-8') as f:
        for link in tqdm(invalid, desc="Сохранение невалидных", unit="ссылка"):
            status = link.status_code or "ERR"
            error = link.error or ""
            f.write(f"{link.text} | {link.url} | {status} | {error} | {link.response_time:.3f}\n")
    
    # JSON отчет
    import json
    report = {
        'summary': {
            'total_links': len(links),
            'valid_links': len(valid),
            'invalid_links': len(invalid),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'avg_response_time': sum(l.response_time for l in links) / len(links) if links else 0
        },
        'links': [l.__dict__ for l in links]
    }
    
    with open('full_report.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Отчеты сохранены:")
    print(f"   • valid_links.txt ({len(valid)} ссылок)")
    print(f"   • invalid_links.txt ({len(invalid)} ссылок)")
    print(f"   • full_report.json (полные данные)")

async def main(url: str, max_connections: int = 200):
    """Основной процесс с прогресс-баром для всех этапов"""
    # Обработчик прерываний
    def signal_handler(sig, frame):
        print("\n\n⚠️ Получен сигнал прерывания. Завершаем...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    print("="*60)
    print("🚀 СВЕРХБЫСТРАЯ ПРОВЕРКА ССЫЛОК С SELENIUM И ПРОГРЕСС-БАРОМ")
    print("="*60)
    print(f"🎯 Целевой URL: {url}")
    print(f"⚡ Макс соединений: {max_connections}")
    print(f"📈 Нет ограничений на количество ссылок")
    print("-"*60)
    
    total_start = time.time()
    
    # Этап 1: Получение ссылок через Selenium
    links = get_links_with_selenium(url)
    if not links:
        print("❌ Не удалось получить ссылки с страницы")
        return
    
    # Этап 2: Сверхбыстрая проверка статусов
    check_start = time.time()
    
    try:
        checked_links = await check_links_ultra_fast(links, max_connections)
    except asyncio.CancelledError:
        print("❗ Проверка была прервана")
        return
    
    check_time = time.time() - check_start
    total_time = time.time() - total_start
    
    # Этап 3: Отчет
    print(f"\n{'='*60}")
    print(f"⏱️  Время проверки: {check_time:.2f} сек")
    print(f"⚡ Скорость: {len(links)/check_time:.1f} ссылок/сек")
    print(f"🏁 Общее время: {total_time:.2f} сек")
    
    report_results(checked_links)
    
    print(f"\n{'='*60}")
    print(f"🎉 ПРОВЕРКА ЗАВЕРШЕНА УСПЕШНО!")
    print(f"{'='*60}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Сверхбыстрая проверка ссылок с прогресс-баром')
    parser.add_argument('url', type=str, help='URL для анализа')
    parser.add_argument('--connections', type=int, default=200, help='Максимальное количество соединений (по умолчанию: 200)')
    args = parser.parse_args()
    
    # Проверка наличия ChromeDriver
    try:
        from selenium import webdriver
    except ImportError:
        print("❌ Ошибка: не установлен selenium")
        print("Установите зависимости: pip install selenium aiohttp tqdm webdriver-manager")
        sys.exit(1)
    
    # Запуск основного процесса
    asyncio.run(main(args.url, args.connections))