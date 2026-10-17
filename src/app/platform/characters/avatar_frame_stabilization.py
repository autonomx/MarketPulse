"""Pixel-stabilize generated avatar frames against their canonical portrait."""
from __future__ import annotations

from app.assets.protocol import AssetStore
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.assets.content import AssetContentUnavailable, materialize_asset
from app.assets import AssetType

_DEFAULT_MOUTH_ANCHOR = {
    "x": 0.5,
    "y": 0.61,
    "width": 0.3,
    "height": 0.17,
}
_SUPPORTED_OUTPUT_FORMATS = {"JPEG", "PNG", "WEBP"}
_PRECISE_VISEMES = {"a", "e", "o", "u", "mbp", "fv", "l", "wq", "other"}
_FALLBACK_MOUTH_ARTICULATION = {
    "mouth_small": 20,
    "mouth_medium": 35,
    "mouth_wide": 50,
}


class AvatarFrameStabilizationError(ValueError):
    """Raised when a generated avatar frame cannot be stabilized safely."""


def stabilize_generated_avatar_frame(
    generated_path: str | Path,
    *,
    reference_asset_id: str,
    variant: str,
    store: AssetStore,
    mouth_anchor: Mapping[str, float] | None = None,
    articulation_percent: float | None = None,
) -> dict[str, Any]:
    """Replace global diffusion drift with a localized, conservative facial edit.

    Generated mouth, blink, and expression images are translation-aligned to the
    canonical portrait. Only the intended facial region is feathered onto the
    canonical image. Mouth frames receive an additional articulation-strength
    clamp and quality gate so a speaking frame cannot become a scream or grin.
    """

    region = avatar_frame_region(variant)
    if region is None:
        return {}

    reference_id = str(reference_asset_id or "").strip()
    if not reference_id:
        raise AvatarFrameStabilizationError("avatar_stabilization_reference_missing")
    reference = store.get_asset(reference_id)
    if reference is None:
        raise AvatarFrameStabilizationError(
            f"avatar_stabilization_reference_not_found:{reference_id}"
        )
    if reference.type != AssetType.IMAGE:
        raise AvatarFrameStabilizationError(
            f"avatar_stabilization_reference_not_image:{reference_id}"
        )

    output_path = Path(generated_path)
    try:
        reference_path = materialize_asset(reference)
    except AssetContentUnavailable:
        raise AvatarFrameStabilizationError(
            f"avatar_stabilization_reference_file_missing:{reference_id}"
        ) from None
    if not output_path.is_file():
        raise AvatarFrameStabilizationError(
            f"avatar_stabilization_output_missing:{output_path}"
        )

    Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat = _pillow()
    try:
        with Image.open(reference_path) as source:
            canonical = ImageOps.exif_transpose(source).convert("RGB").copy()
        with Image.open(output_path) as source:
            output_format = str(source.format or "PNG").upper()
            generated = ImageOps.exif_transpose(source).convert("RGB").copy()
    except (OSError, ValueError) as exc:
        raise AvatarFrameStabilizationError(
            f"avatar_stabilization_invalid_image:{exc}"
        ) from exc

    if generated.size != canonical.size:
        generated = generated.resize(canonical.size, Image.Resampling.LANCZOS)

    normalized_anchor = normalize_mouth_anchor(mouth_anchor)
    comparison_boxes = _comparison_boxes(region, canonical.size, normalized_anchor)
    offset_x, offset_y = _best_translation(
        canonical,
        generated,
        comparison_boxes,
        ImageChops,
        ImageFilter,
        ImageStat,
    )
    aligned = canonical.copy()
    aligned.paste(generated, (offset_x, offset_y))
    aligned = _match_reference_tone(
        canonical,
        aligned,
        comparison_boxes,
        Image,
        ImageStat,
    )

    region_box = _region_box(region, canonical.size, normalized_anchor)
    articulation = _normalized_articulation(variant, articulation_percent)
    quality_metrics: dict[str, float] = {}
    blend_strength = 1.0
    if region == "mouth":
        quality_metrics = _mouth_quality_metrics(
            canonical,
            aligned,
            region_box,
            ImageChops,
        )
        if _base_viseme(variant) is not None:
            _validate_mouth_quality(quality_metrics, articulation)
        blend_strength = _mouth_blend_strength(articulation)
        aligned = Image.blend(canonical, aligned, blend_strength)

    mask = Image.new("L", canonical.size, 0)
    ImageDraw.Draw(mask).ellipse(region_box, fill=255)
    feather = _feather_radius(region, canonical.size)
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    stabilized = Image.composite(aligned, canonical, mask)

    _save_atomic(stabilized, output_path, output_format)
    return {
        "avatar_frame_stabilized": True,
        "avatar_stabilization_region": region,
        "avatar_stabilization_reference_asset_id": reference_id,
        "avatar_alignment_dx": offset_x,
        "avatar_alignment_dy": offset_y,
        "avatar_stabilization_feather_px": feather,
        "avatar_stabilization_box": {
            "left": region_box[0],
            "top": region_box[1],
            "right": region_box[2],
            "bottom": region_box[3],
        },
        "avatar_mouth_anchor": normalized_anchor,
        "avatar_articulation_percent": articulation,
        "avatar_articulation_blend_strength": round(blend_strength, 4),
        **{f"avatar_quality_{key}": value for key, value in quality_metrics.items()},
    }


def avatar_frame_region(variant: str) -> str | None:
    normalized = str(variant or "").strip().lower()
    if not normalized or normalized == "base":
        return None
    if normalized.startswith("mouth_") or _base_viseme(normalized) is not None:
        return "mouth"
    if normalized.startswith("blink_"):
        return "eyes"
    if normalized.startswith("expression_"):
        return "face"
    return None


def normalize_mouth_anchor(values: Mapping[str, float] | None = None) -> dict[str, float]:
    supplied = values or {}
    return {
        "x": _normalized_value(supplied.get("x"), _DEFAULT_MOUTH_ANCHOR["x"], 0.2, 0.8),
        "y": _normalized_value(supplied.get("y"), _DEFAULT_MOUTH_ANCHOR["y"], 0.35, 0.85),
        "width": _normalized_value(
            supplied.get("width"),
            _DEFAULT_MOUTH_ANCHOR["width"],
            0.12,
            0.7,
        ),
        "height": _normalized_value(
            supplied.get("height"),
            _DEFAULT_MOUTH_ANCHOR["height"],
            0.08,
            0.5,
        ),
    }


def _pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat
    except ImportError as exc:
        raise AvatarFrameStabilizationError(
            "avatar_stabilization_runtime_missing:Pillow"
        ) from exc
    return Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat


def _base_viseme(variant: str) -> str | None:
    base = str(variant or "").strip().lower().split("_", 1)[0]
    return base if base in _PRECISE_VISEMES else None


def _normalized_articulation(
    variant: str,
    value: float | None,
) -> int:
    normalized_variant = str(variant or "").strip().lower()
    if value is None:
        if normalized_variant in _FALLBACK_MOUTH_ARTICULATION:
            value = _FALLBACK_MOUTH_ARTICULATION[normalized_variant]
        else:
            suffix = normalized_variant.rsplit("_", 1)
            if len(suffix) == 2 and suffix[1].isdigit():
                value = int(suffix[1])
            elif _base_viseme(normalized_variant) is not None:
                value = 60
            else:
                value = 100
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 60 if _base_viseme(variant) is not None else 100


def _normalized_value(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    if not minimum <= number <= maximum:
        number = default
    return round(number, 4)


def _comparison_boxes(
    region: str,
    size: tuple[int, int],
    anchor: Mapping[str, float],
) -> list[tuple[int, int, int, int]]:
    width, height = size
    if region == "mouth":
        return [
            _fraction_box(size, 0.25, 0.2, 0.75, 0.52),
            _fraction_box(size, 0.2, 0.72, 0.8, 0.9),
        ]
    if region == "eyes":
        mouth_top = max(0.5, float(anchor["y"]) - float(anchor["height"]) / 2)
        return [
            _fraction_box(size, 0.28, mouth_top, 0.72, min(0.82, mouth_top + 0.24)),
            _fraction_box(size, 0.15, 0.1, 0.85, 0.27),
        ]
    return [
        _fraction_box(size, 0.08, 0.08, 0.92, 0.24),
        _fraction_box(size, 0.08, 0.72, 0.92, 0.94),
        (
            max(0, round(width * 0.08)),
            max(0, round(height * 0.22)),
            max(1, round(width * 0.25)),
            max(1, round(height * 0.75)),
        ),
        (
            min(width - 1, round(width * 0.75)),
            max(0, round(height * 0.22)),
            min(width, round(width * 0.92)),
            max(1, round(height * 0.75)),
        ),
    ]


def _fraction_box(
    size: tuple[int, int],
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> tuple[int, int, int, int]:
    width, height = size
    return (
        max(0, min(width - 1, round(width * left))),
        max(0, min(height - 1, round(height * top))),
        max(1, min(width, round(width * right))),
        max(1, min(height, round(height * bottom))),
    )


def _best_translation(
    canonical: Any,
    generated: Any,
    boxes: list[tuple[int, int, int, int]],
    ImageChops: Any,
    ImageFilter: Any,
    ImageStat: Any,
) -> tuple[int, int]:
    width, height = canonical.size
    radius = max(2, min(10, round(min(width, height) * 0.01)))
    canonical_edges = canonical.convert("L").filter(ImageFilter.FIND_EDGES)
    generated_edges = generated.convert("L").filter(ImageFilter.FIND_EDGES)
    best_offset = (0, 0)
    best_score = float("inf")

    for offset_y in range(-radius, radius + 1):
        for offset_x in range(-radius, radius + 1):
            score = 0.0
            valid = True
            for left, top, right, bottom in boxes:
                candidate_box = (
                    left - offset_x,
                    top - offset_y,
                    right - offset_x,
                    bottom - offset_y,
                )
                if (
                    candidate_box[0] < 0
                    or candidate_box[1] < 0
                    or candidate_box[2] > width
                    or candidate_box[3] > height
                ):
                    valid = False
                    break
                baseline = canonical_edges.crop((left, top, right, bottom))
                candidate = generated_edges.crop(candidate_box)
                difference = ImageChops.difference(baseline, candidate)
                area = max(1, (right - left) * (bottom - top))
                score += float(ImageStat.Stat(difference).mean[0]) * area
            if valid and score < best_score:
                best_score = score
                best_offset = (offset_x, offset_y)
    return best_offset


def _match_reference_tone(
    canonical: Any,
    aligned: Any,
    boxes: list[tuple[int, int, int, int]],
    Image: Any,
    ImageStat: Any,
) -> Any:
    canonical_means = _weighted_channel_means(canonical, boxes, ImageStat)
    aligned_means = _weighted_channel_means(aligned, boxes, ImageStat)
    deltas = [
        max(-14, min(14, round(reference - candidate)))
        for reference, candidate in zip(canonical_means, aligned_means, strict=True)
    ]
    if not any(deltas):
        return aligned

    adjusted_channels = []
    for channel, delta in zip(aligned.split(), deltas, strict=True):
        table = [max(0, min(255, value + delta)) for value in range(256)]
        adjusted_channels.append(channel.point(table))
    return Image.merge("RGB", tuple(adjusted_channels))


def _weighted_channel_means(
    image: Any,
    boxes: list[tuple[int, int, int, int]],
    ImageStat: Any,
) -> tuple[float, float, float]:
    totals = [0.0, 0.0, 0.0]
    total_area = 0
    for box in boxes:
        area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
        means = ImageStat.Stat(image.crop(box)).mean
        for index in range(3):
            totals[index] += float(means[index]) * area
        total_area += area
    return tuple(total / max(1, total_area) for total in totals)  # type: ignore[return-value]


def _mouth_quality_metrics(
    canonical: Any,
    aligned: Any,
    region_box: tuple[int, int, int, int],
    ImageChops: Any,
) -> dict[str, float]:
    reference_crop = canonical.crop(region_box)
    candidate_crop = aligned.crop(region_box)
    area = max(1, reference_crop.width * reference_crop.height)

    reference_gray = list(reference_crop.convert("L").getdata())
    candidate_gray = list(candidate_crop.convert("L").getdata())
    reference_rgb = list(reference_crop.getdata())
    candidate_rgb = list(candidate_crop.getdata())

    def dark_fraction(values: list[int]) -> float:
        return sum(value < 62 for value in values) / area

    def bright_neutral_fraction(values: list[tuple[int, int, int]]) -> float:
        return sum(
            max(pixel) - min(pixel) < 34 and sum(pixel) / 3 > 214
            for pixel in values
        ) / area

    changed = list(
        ImageChops.difference(reference_crop, candidate_crop).convert("L").getdata()
    )
    return {
        "dark_delta": round(
            max(0.0, dark_fraction(candidate_gray) - dark_fraction(reference_gray)),
            4,
        ),
        "bright_delta": round(
            max(
                0.0,
                bright_neutral_fraction(candidate_rgb)
                - bright_neutral_fraction(reference_rgb),
            ),
            4,
        ),
        "changed_fraction": round(sum(value > 34 for value in changed) / area, 4),
    }


def _validate_mouth_quality(metrics: Mapping[str, float], articulation: int) -> None:
    factor = articulation / 100
    max_dark_delta = 0.085 + 0.14 * factor
    max_bright_delta = 0.07 + 0.12 * factor
    max_changed_fraction = 0.58 + 0.34 * factor
    dark_delta = float(metrics.get("dark_delta", 0.0))
    bright_delta = float(metrics.get("bright_delta", 0.0))
    changed_fraction = float(metrics.get("changed_fraction", 0.0))
    broad_extreme_change = changed_fraction > max_changed_fraction and (
        dark_delta > max_dark_delta * 0.55
        or bright_delta > max_bright_delta * 0.55
    )
    if (
        dark_delta <= max_dark_delta
        and bright_delta <= max_bright_delta
        and not broad_extreme_change
    ):
        return
    raise AvatarFrameStabilizationError(
        "avatar_frame_quality_rejected:"
        f"articulation={articulation};"
        f"dark_delta={dark_delta:.4f};"
        f"bright_delta={bright_delta:.4f};"
        f"changed_fraction={changed_fraction:.4f}"
    )


def _mouth_blend_strength(articulation: int) -> float:
    return max(0.42, min(0.85, 0.4 + articulation * 0.0045))


def _region_box(
    region: str,
    size: tuple[int, int],
    anchor: Mapping[str, float],
) -> tuple[int, int, int, int]:
    width, height = size
    mouth_x = float(anchor["x"])
    mouth_y = float(anchor["y"])
    mouth_width = float(anchor["width"])
    mouth_height = float(anchor["height"])

    if region == "mouth":
        center_x, center_y = mouth_x, mouth_y
        region_width = max(0.18, min(0.34, mouth_width * 0.84))
        region_height = max(0.09, min(0.18, mouth_height * 0.72))
    elif region == "eyes":
        center_x = mouth_x
        center_y = max(0.2, mouth_y - 0.205)
        region_width = max(0.46, mouth_width * 1.5)
        region_height = max(0.14, mouth_height * 0.82)
    else:
        center_x, center_y = 0.5, 0.47
        region_width, region_height = 0.64, 0.65

    half_width = region_width / 2
    half_height = region_height / 2
    return (
        max(0, round(width * (center_x - half_width))),
        max(0, round(height * (center_y - half_height))),
        min(width, round(width * (center_x + half_width))),
        min(height, round(height * (center_y + half_height))),
    )


def _feather_radius(region: str, size: tuple[int, int]) -> int:
    if region == "mouth":
        scale = 0.0065
    elif region == "eyes":
        scale = 0.009
    else:
        scale = 0.02
    return max(3, round(min(size) * scale))


def _save_atomic(image: Any, output_path: Path, output_format: str) -> None:
    normalized_format = output_format if output_format in _SUPPORTED_OUTPUT_FORMATS else "PNG"
    temporary = output_path.with_name(
        f".{output_path.name}.stabilized-{uuid.uuid4().hex}.tmp"
    )
    save_kwargs: dict[str, Any] = {}
    if normalized_format == "PNG":
        save_kwargs["optimize"] = True
    elif normalized_format == "JPEG":
        save_kwargs.update({"quality": 95, "subsampling": 0})
    elif normalized_format == "WEBP":
        save_kwargs.update({"quality": 95, "method": 4})

    try:
        image.save(temporary, format=normalized_format, **save_kwargs)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "AvatarFrameStabilizationError",
    "avatar_frame_region",
    "normalize_mouth_anchor",
    "stabilize_generated_avatar_frame",
]
