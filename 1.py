import requests
from bs4 import BeautifulSoup

def decode_message(url):
    # Fetch and parse table
    soup = BeautifulSoup(requests.get(url).text, 'html.parser')
    rows = soup.find_all('tr')[1:] # Skip header
    
    # Store coords and chars
    data = []
    for r in rows:
        cells = [c.text.strip() for c in r.find_all('td')]
        if len(cells) == 3:
            data.append((int(cells[0]), int(cells[2]), cells[1]))

    # Build and print grid
    max_x = max(d[0] for d in data)
    max_y = max(d[1] for d in data)
    grid = [[' ' for _ in range(max_x + 1)] for _ in range(max_y + 1)]
    
    for x, y, char in data:
        grid[y][x] = char

    for r in range(max_y, -1, -1):
        print("".join(grid[r]))

# Example usage:
decode_message("https://docs.google.com/document/d/e/2PACX-1vSvM5gDlNvt7npYHhp_XfsJvuntUhq184By5xO_pA4b_gCWeXb6dM6ZxwN8rE6S4ghUsCj2VKR21oEP/pub")