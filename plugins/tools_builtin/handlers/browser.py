import logging
import webbrowser
import httpx
import re
import html
import asyncio
from urllib.parse import unquote

logger = logging.getLogger("aurora.tools.browser")

def strip_html(html_content: str) -> str:
    """Basic HTML tag stripping and whitespace collapsing."""
    # Remove scripts and styles
    text = re.sub(r'<style.*?>.*?</style>', ' ', html_content, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<script.*?>.*?</script>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    # Remove all other tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def handle(args: dict) -> dict:
    action = args.get("action")
    if not action:
        logger.warning("No action specified in browser tool")
        return {"ok": False, "error": "No action specified"}

    logger.info(f"Executing browser tool action: {action}")

    try:
        if action == "open_url":
            url = args.get("url")
            if not url:
                logger.warning("URL required for open_url")
                return {"ok": False, "error": "URL required"}
            
            webbrowser.open(url)
            return {"ok": True, "data": f"Opened {url} in default browser"}
            
        elif action == "get_page_content":
            url = args.get("url")
            if not url:
                logger.warning("URL required for get_page_content")
                return {"ok": False, "error": "URL required"}
            headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
                response = await client.get(url)
                response.raise_for_status()
                
            text = strip_html(response.text)
            
            # Cap at 8KB
            if len(text) > 8192:
                text = text[:8192] + "..."
                
            return {"ok": True, "data": text}
            
        elif action == "search_web":
            query = args.get("query")
            if not query:
                logger.warning("Query required for search_web")
                return {"ok": False, "error": "Query required"}
                
            max_results = int(args.get("max_results", 5))
            
            async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as client:
                response = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query}
                )
                response.raise_for_status()
                
            html_content = response.text
            results = []
            
            # Parse DuckDuckGo HTML results (match any div whose class contains result__body)
            blocks = re.findall(r'<div[^>]*class="[^"]*result__body[^"]*"[^>]*>(.*?)</div>\s*<!-- \.result__body -->', html_content, flags=re.DOTALL | re.IGNORECASE)
            if not blocks:
                blocks = re.findall(r'<div[^>]*class="[^"]*result__body[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*result__body|$)', html_content, flags=re.DOTALL | re.IGNORECASE)

            for block in blocks[:max_results]:
                # Title and URL from result__a link
                title_match = re.search(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.DOTALL | re.IGNORECASE)
                if not title_match:
                    title_match = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.DOTALL | re.IGNORECASE)

                if title_match:
                    raw_url = title_match.group(1)
                    title = strip_html(title_match.group(2))
                    if "uddg=" in raw_url:
                        url = unquote(raw_url.split("uddg=")[1].split("&")[0])
                    else:
                        url = raw_url

                    # Snippet
                    snippet_match = re.search(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', block, flags=re.DOTALL | re.IGNORECASE)
                    snippet = strip_html(snippet_match.group(1)) if snippet_match else ""

                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet
                    })

            return {"ok": True, "data": results}
            
        else:
            logger.warning(f"Unknown browser action: {action}")
            return {"ok": False, "error": f"Unknown action: {action}"}
            
    except Exception as e:
        logger.warning(f"Browser tool error: {str(e)}", exc_info=True)
        return {"ok": False, "error": str(e)}
