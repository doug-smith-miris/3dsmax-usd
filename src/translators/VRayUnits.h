//
// Copyright 2026 Miris Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
#pragma once

#include <pxr/pxr.h>

PXR_NAMESPACE_OPEN_SCOPE

// MAX-LIT-GEOLIGHT-022 — ONE V-Ray light-unit converter, shared.
//
// This logic previously existed twice: once in VRayLightWriter (for light OBJECTS) and once in
// LastResortMtlxShaderWriter (for light MATERIALS), with a comment on each telling the reader to
// "keep the two in sync". They are now the same function, because a geometry light and the emissive
// surface it came from must agree -- if they disagree, the same emitter contributes different energy
// depending on which code path happened to handle it, which is exactly the class of bug that made
// the arena's geometry lights emit at the default 1.0 while their surfaces carried 190.
namespace MaxUsdVRay {

constexpr float kPi          = 3.14159265358979323846f;
constexpr float kPhotopicK   = 683.0f;   // photopic peak, lm/W
constexpr float kCeiling     = 10000.0f; // nit-scale, tone-mapper safe

// MAX-LIT-DIMNESS-014: default-mode (units=0) V-Ray values are an arbitrary artistic scale rather
// than a nit, because V-Ray's colour mapping brightens beyond the raw radiance. This gain brings
// them onto Karma's nit scale.
//
// NOTE ON ITS VALUE: 3.8 was hand-tuned against a whole-frame mean, and there is reason to think it
// is too high -- the court measures ~1.8x brighter than the V-Ray reference. That comparison is
// NOT conclusive, because the reference PNG carries an unknown exposure constant, so absolute
// levels cannot be compared without an anchor. Deliberately left at 3.8 here: changing it needs an
// absolute reference (a matched V-Ray render with known exposure, or a physical-camera match), not
// another eyeball pass. Only the RATIO between direct and indirect light is anchor-free, and that
// ratio is what MAX-LIT-GEOLIGHT-022 fixes.
constexpr float kUnits0Gain  = 3.8f;

/// Convert a V-Ray light multiplier + units enum to a UsdLux intensity (nits).
///
/// \param multiplier the V-Ray `multiplier` value
/// \param units      the V-Ray `units` enum (0 default, 1 lumens, 2 cd/m2, 3 watts, 4 W/m2/sr)
/// \param hasUnits   false when the class has no `.units` property at all (older V-Ray) -- falls
///                   through to pass-through so pre-units scenes keep their previous behaviour.
///                   NOTE: a VRayLightMtl exposes no units either, but callers should still pass
///                   units=0 / hasUnits=true for it, because the emissive-surface path applies the
///                   units=0 gain -- a light and its own surface must not disagree.
inline float UnitsToNits(float multiplier, int units, bool hasUnits)
{
    float base = multiplier;
    if (hasUnits) {
        switch (units) {
        case 0: base = multiplier * kUnits0Gain; break;          // artistic scale -> calibrated
        case 1: base = multiplier / kPi; break;                  // lumens -> nits (Lambertian)
        case 2: base = multiplier; break;                        // cd/m2 == nits already
        case 3: base = (multiplier * kPhotopicK) / kPi; break;   // watts -> lumens -> nits
        case 4: base = multiplier * kPhotopicK; break;           // W/m2/sr -> nits
        default: base = multiplier; break;
        }
    }
    if (base < 0.f) {
        base = 0.f;   // Max's UI is non-negative
    }
    if (base > kCeiling) {
        base = kCeiling;
    }
    return base;
}

} // namespace MaxUsdVRay

PXR_NAMESPACE_CLOSE_SCOPE
