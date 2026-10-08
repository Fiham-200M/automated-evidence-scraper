import asyncio
import json
import sys
from pathlib import Path

# Existing project directory
PROJECT_DIR = Path(r"C:\Users\AI Fiham\Pictures\Playwright")

# Make existing project modules importable
sys.path.insert(0, str(PROJECT_DIR))

import scrape_links


async def run_brand(
    brand: str,
    url: str,
    title: str = "",
    rank: int = 1,
):
    """
    Run the existing deterministic scraper for ONE URL.

    Reuses:
      - scrape_links.launch_browser()
      - scrape_links.warmup_browsing()
      - scrape_links.scrape_and_save()

    No scraping/AMP logic is duplicated here.
    """

    out_root = PROJECT_DIR / "link_scrape_output"
    brand_out_dir = out_root / scrape_links.safe_filename(brand)
    brand_out_dir.mkdir(parents=True, exist_ok=True)

    playwright = None
    context = None

    try:
        print("=" * 70)
        print("HERMES SINGLE-URL BRAND RUNNER")
        print("=" * 70)
        print(f"Brand : {brand}")
        print(f"Rank  : {rank}")
        print(f"Title : {title}")
        print(f"URL   : {url}")
        print(f"Output: {brand_out_dir}")
        print()

        # Reuse existing browser implementation
        playwright, context, initial_page = await scrape_links.launch_browser()

        # Reuse existing warmup behavior
        await scrape_links.warmup_browsing(initial_page)

        # Reuse the existing complete scraper
        await scrape_links.scrape_and_save(
            page=initial_page,
            url=url,
            brand=brand,
            rank=rank,
            title=title,
            brand_out_dir=brand_out_dir,
        )

        # Locate the metadata file produced by scrape_and_save()
        safe_title = (
            scrape_links.safe_filename(title)
            if title
            else f"rank{rank}"
        )

        base_name = f"{str(rank).zfill(3)}_{safe_title}"
        metadata_path = brand_out_dir / f"{base_name}.json"

        result = {
            "status": "completed",
            "brand": brand,
            "rank": rank,
            "title": title,
            "url": url,
            "output_directory": str(brand_out_dir),
            "metadata": str(metadata_path),
        }

        if metadata_path.exists():
            try:
                result["evidence"] = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except Exception as e:
                result["metadata_read_error"] = str(e)

        print()
        print(json.dumps(result, indent=2, ensure_ascii=False))

        return result

    except Exception as e:
        result = {
            "status": "failed",
            "brand": brand,
            "rank": rank,
            "title": title,
            "url": url,
            "error": str(e),
        }

        print()
        print(json.dumps(result, indent=2, ensure_ascii=False))

        return result

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass


async def main():
    if len(sys.argv) < 3:
        print(
            "Usage:\n"
            "  python hermes_brand_runner.py <brand> <url> [title] [rank]"
        )
        sys.exit(2)

    brand = sys.argv[1]
    url = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) >= 4 else ""
    rank = int(sys.argv[4]) if len(sys.argv) >= 5 else 1

    await run_brand(
        brand=brand,
        url=url,
        title=title,
        rank=rank,
    )


if __name__ == "__main__":
    asyncio.run(main())
