//
// Copyright 2023 Autodesk
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
#include <gtest/gtest.h>

#include <MaxUsd/Utilities/MaterialUtils.h>
#include "TestUtils.h"
#include "Mocks/MockStdMat.h"
#include "Mocks/MockINode.h"

using namespace MaxUsd;

TEST(MaterialUtilsTest, CreateSubsetName)
{
	// MAX-GEO-003: null material should return mat_{maxScriptId}
	// (replaces the legacy underscore-wrapped `_{N}_` pattern).
	EXPECT_EQ(MaterialUtils::CreateSubsetName(nullptr, 0), "mat_1");
	EXPECT_EQ(MaterialUtils::CreateSubsetName(nullptr, 5), "mat_6");
	EXPECT_EQ(MaterialUtils::CreateSubsetName(nullptr, 10), "mat_11");

	// MAX-GEO-003: single (non-Multi) material should return mat_{maxScriptId}
	// -- same fallback as the null case.
	MSTR mtlName = L"some material name";
	std::string stringMtlName = mtlName.ToCStr().data();
	MockStdMat mockMtl;
	Mtl* mtl = static_cast<Mtl*>(&mockMtl);
	mtl->SetName(mtlName);
	EXPECT_EQ(MaterialUtils::CreateSubsetName(mtl, 0), "mat_1");
	EXPECT_EQ(MaterialUtils::CreateSubsetName(mtl, 5), "mat_6");
	EXPECT_EQ(MaterialUtils::CreateSubsetName(mtl, 10), "mat_11");

	// MAX-GEO-003: multi material without slot name returns
	// mat_{maxScriptId}_{subMaterialName} (replaces the legacy
	// `_{N}_{subMaterialName}` which still emitted a leading underscore).
	MockMultiMtl* mockMultiMtl = new MockMultiMtl();
	Mtl* multiMtl = static_cast<Mtl*>(mockMultiMtl);
	mockMultiMtl->AddMtl(mtl, 2, nullptr);
	EXPECT_EQ(MaterialUtils::CreateSubsetName(mockMultiMtl, 2), "mat_3_some_material_name");

	// multi material with slot name should return the name (unchanged behavior).
	std::wstring slotName = L"material slot name";
	mockMultiMtl->AddMtl(mtl, 3, slotName.c_str());
	EXPECT_EQ(MaterialUtils::CreateSubsetName(mockMultiMtl, 3), "material_slot_name");
}