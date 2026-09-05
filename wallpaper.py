
import ctypes
import io
import math
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


BACKGROUND = (13, 13, 15)


def download_avatar(url):
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": "Liam-Wallpaper/1.0",
        },
    )

    response.raise_for_status()

    image = Image.open(
        io.BytesIO(response.content)
    )

    return image.convert("RGB")


def crop_square(image):
    width, height = image.size
    side = min(width, height)

    left = (width - side) // 2
    top = (height - side) // 2

    return image.crop(
        (
            left,
            top,
            left + side,
            top + side,
        )
    )


def make_circle(image, size):
    image = crop_square(image)

    image = ImageOps.fit(
        image,
        (size, size),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    zoom = 1.18

    zoomed_size = int(size * zoom)

    image = ImageOps.fit(
        image,
        (zoomed_size, zoomed_size),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    image = image.resize(
        (size, size),
        Image.Resampling.LANCZOS,
    )

    mask = Image.new(
        "L",
        (size, size),
        0,
    )

    mask_draw = ImageDraw.Draw(mask)

    mask_draw.ellipse(
        (0, 0, size - 1, size - 1),
        fill=255,
    )

    output = Image.new(
        "RGBA",
        (size, size),
        (0, 0, 0, 0),
    )

    output.paste(
        image,
        (0, 0),
        mask,
    )

    return output


def get_font(size):
    """
    Try a few common Windows fonts.
    Falls back to Pillow's default font if necessary.
    """

    font_paths = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]

    for font_path in font_paths:
        try:
            return ImageFont.truetype(
                font_path,
                size,
            )
        except Exception:
            continue

    return ImageFont.load_default()


def fit_username(username, max_width, starting_size):
    """
    Shrink long usernames until they fit inside
    the avatar cell.
    """

    size = starting_size

    while size >= 12:
        font = get_font(size)

        dummy = Image.new(
            "RGB",
            (10, 10),
        )

        draw = ImageDraw.Draw(dummy)

        bbox = draw.textbbox(
            (0, 0),
            username,
            font=font,
        )

        text_width = bbox[2] - bbox[0]

        if text_width <= max_width:
            return font

        size -= 2

    return get_font(12)


def draw_username(
    canvas,
    username,
    x,
    y,
    avatar_size,
    cell_width,
):
    """
    Draw the Discord username centered underneath
    the profile picture.
    """

    if not username:
        username = "Discord user"

    username = str(username)

    max_text_width = int(
        cell_width * 0.85
    )

    starting_size = max(
        22,
        int(avatar_size * 0.085),
    )

    font = fit_username(
        username,
        max_text_width,
        starting_size,
    )

    draw = ImageDraw.Draw(canvas)

    bbox = draw.textbbox(
        (0, 0),
        username,
        font=font,
    )

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    text_x = int(
        x
        + (avatar_size - text_width) / 2
    )

    text_y = (
        y
        + avatar_size
        + 18
    )

    shadow_offset = 2

    draw.text(
        (
            text_x + shadow_offset,
            text_y + shadow_offset,
        ),
        username,
        font=font,
        fill=(0, 0, 0),
    )

    draw.text(
        (text_x, text_y),
        username,
        font=font,
        fill=(255, 255, 255),
    )


def generate_wallpaper(
    friends,
    output_path,
    width=1920,
    height=1080,
):
    """
    Generate the wallpaper.

    Each friend gets:
        - a large circular Discord avatar
        - their Discord username underneath

    There is intentionally:
        - no grid
        - no "waiting for friends" text
        - no cards
        - no extra UI
    """

    canvas = Image.new(
        "RGB",
        (width, height),
        BACKGROUND,
    )

    if not friends:
        canvas.save(
            output_path,
            "PNG",
        )

        return

    count = len(friends)

    columns = max(
        1,
        math.ceil(
            math.sqrt(
                count * width / height
            )
        ),
    )

    rows = math.ceil(
        count / columns
    )

    cell_width = width / columns
    cell_height = height / rows

    avatar_size = int(
        min(
            cell_width,
            cell_height,
        ) * 0.70
    )

    for index, friend in enumerate(friends):
        try:
            avatar_url = friend.get(
                "avatar_url"
            )

            if not avatar_url:
                continue

            avatar = download_avatar(
                avatar_url
            )

            avatar = make_circle(
                avatar,
                avatar_size,
            )

            column = index % columns
            row = index // columns

            x = int(
                column * cell_width
                + (
                    cell_width
                    - avatar_size
                ) / 2
            )

            y = int(
                row * cell_height
                + (
                    cell_height
                    - avatar_size
                ) / 2
            )

            canvas.paste(
                avatar,
                (x, y),
                avatar,
            )

            username = friend.get(
                "username",
                "Discord user",
            )

            draw_username(
                canvas=canvas,
                username=username,
                x=x,
                y=y,
                avatar_size=avatar_size,
                cell_width=cell_width,
            )

        except Exception as exc:
            print(
                "Could not load avatar for "
                f"{friend.get('username', 'friend')}: "
                f"{exc}"
            )

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    canvas.save(
        output_path,
        "PNG",
    )


def set_windows_wallpaper(path):
    """
    Set the generated image as the Windows desktop wallpaper.
    """

    
    if not hasattr(ctypes, "windll"):
        return

    absolute_path = str(
        Path(path).resolve()
    )

    result = ctypes.windll.user32.SystemParametersInfoW(
        20,  
        0,
        absolute_path,
        3,   
    )

    if not result:
        raise RuntimeError(
            "Windows failed to set the wallpaper."
        )