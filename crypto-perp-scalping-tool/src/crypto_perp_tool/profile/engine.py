from collections import defaultdict

from crypto_perp_tool.types import ProfileLevel, ProfileLevelType


class VolumeProfileEngine:
    def __init__(self, bin_size: float, value_area_ratio: float = 0.70) -> None:
        if bin_size <= 0:
            raise ValueError("bin_size must be positive")
        if not 0 < value_area_ratio <= 1:
            raise ValueError("value_area_ratio must be in (0, 1]")

        self.bin_size = bin_size
        self.value_area_ratio = value_area_ratio
        self._volume_by_bin: dict[float, float] = defaultdict(float)

    def add_trade(self, price: float, quantity: float) -> None:
        if quantity <= 0:
            return
        self._volume_by_bin[self._bin_price(price)] += quantity

    def levels(self, window: str) -> tuple[ProfileLevel, ...]:
        if not self._volume_by_bin:
            return ()

        bins = sorted(self._volume_by_bin)
        volumes = self._volume_by_bin
        total_volume = sum(volumes.values())
        average_volume = total_volume / len(volumes)
        poc_price = max(bins, key=lambda price: volumes[price])
        levels = [
            self._level(ProfileLevelType.POC, poc_price, volumes[poc_price] / average_volume, window)
        ]

        val_price, vah_price = self._value_area_bounds(bins, poc_price, total_volume)
        levels.append(self._level(ProfileLevelType.VAL, val_price, volumes[val_price] / average_volume, window))
        levels.append(self._level(ProfileLevelType.VAH, vah_price, volumes[vah_price] / average_volume, window))

        for index, price in enumerate(bins):
            left = volumes[bins[index - 1]] if index > 0 else None
            right = volumes[bins[index + 1]] if index < len(bins) - 1 else None
            if left is None or right is None:
                continue

            volume = volumes[price]
            ratio = volume / average_volume
            if volume > left and volume > right and ratio >= 1.25 and price != poc_price:
                levels.append(self._level(ProfileLevelType.HVN, price, ratio, window))
            if volume < left and volume < right and ratio <= 0.55:
                levels.append(self._level(ProfileLevelType.LVN, price, ratio, window))

        return tuple(levels)

    def _bin_price(self, price: float) -> float:
        return round(price / self.bin_size) * self.bin_size

    def _level(
        self,
        level_type: ProfileLevelType,
        price: float,
        strength: float,
        window: str,
    ) -> ProfileLevel:
        half_bin = self.bin_size / 2
        return ProfileLevel(
            type=level_type,
            price=price,
            lower_bound=price - half_bin,
            upper_bound=price + half_bin,
            strength=strength,
            window=window,
        )

    def _value_area_bounds(
        self,
        bins: list[float],
        poc_price: float,
        total_volume: float,
    ) -> tuple[float, float]:
        volumes = self._volume_by_bin
        target_volume = total_volume * self.value_area_ratio
        included = {poc_price}
        included_volume = volumes[poc_price]
        poc_index = bins.index(poc_price)
        lower_index = poc_index
        upper_index = poc_index

        while included_volume < target_volume and (lower_index > 0 or upper_index < len(bins) - 1):
            lower_candidate = bins[lower_index - 1] if lower_index > 0 else None
            upper_candidate = bins[upper_index + 1] if upper_index < len(bins) - 1 else None
            lower_volume = volumes[lower_candidate] if lower_candidate is not None else -1
            upper_volume = volumes[upper_candidate] if upper_candidate is not None else -1

            if upper_volume >= lower_volume:
                upper_index += 1
                price = bins[upper_index]
            else:
                lower_index -= 1
                price = bins[lower_index]
            included.add(price)
            included_volume += volumes[price]

        return min(included), max(included)
