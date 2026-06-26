/**
 * Unit tests for FeatureSchema (Task 4.3).
 */
#include <fstream>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "strategy/FeatureSchema.hpp"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void writeSchema(const std::string& path,
                        const std::vector<std::string>& names) {
    std::ofstream f(path);
    f << "{\n  \"features\": [\n";
    for (std::size_t i = 0; i < names.size(); ++i) {
        f << "    {\"name\": \"" << names[i] << "\"}";
        if (i + 1 < names.size()) f << ",";
        f << "\n";
    }
    f << "  ]\n}\n";
}

static const std::string SCHEMA_PATH = "/tmp/test_feature_schema.json";

// ---------------------------------------------------------------------------
// loadFromJSON
// ---------------------------------------------------------------------------

TEST(FeatureSchemaLoad, LoadsCorrectFeatureCount) {
    writeSchema(SCHEMA_PATH, {"a", "b", "c"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_EQ(schema.features.size(), 3u);
}

TEST(FeatureSchemaLoad, LoadsFeatureNamesInOrder) {
    writeSchema(SCHEMA_PATH, {"high", "low", "close"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_EQ(schema.features[0], "high");
    EXPECT_EQ(schema.features[1], "low");
    EXPECT_EQ(schema.features[2], "close");
}

TEST(FeatureSchemaLoad, MissingFileThrowsRuntimeError) {
    EXPECT_THROW(FeatureSchema::loadFromJSON("/tmp/no_such_file.json"),
                 std::runtime_error);
}

TEST(FeatureSchemaLoad, EmptyFeaturesArrayThrowsRuntimeError) {
    std::ofstream f(SCHEMA_PATH);
    f << "{\"features\": []}\n";
    f.close();
    EXPECT_THROW(FeatureSchema::loadFromJSON(SCHEMA_PATH), std::runtime_error);
}

// ---------------------------------------------------------------------------
// validate
// ---------------------------------------------------------------------------

TEST(FeatureSchemaValidate, ExactMatchDoesNotThrow) {
    writeSchema(SCHEMA_PATH, {"a", "b", "c"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_NO_THROW(schema.validate({"a", "b", "c"}));
}

TEST(FeatureSchemaValidate, CountMismatchThrowsInvalidArgument) {
    writeSchema(SCHEMA_PATH, {"a", "b", "c"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_THROW(schema.validate({"a", "b"}), std::invalid_argument);
}

TEST(FeatureSchemaValidate, NameMismatchThrowsInvalidArgument) {
    writeSchema(SCHEMA_PATH, {"a", "b", "c"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_THROW(schema.validate({"a", "X", "c"}), std::invalid_argument);
}

TEST(FeatureSchemaValidate, ErrorMessageContainsMismatchedName) {
    writeSchema(SCHEMA_PATH, {"expected_name", "b"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    try {
        schema.validate({"wrong_name", "b"});
        FAIL() << "Expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_NE(std::string(e.what()).find("expected_name"), std::string::npos);
        EXPECT_NE(std::string(e.what()).find("wrong_name"),    std::string::npos);
    }
}

TEST(FeatureSchemaValidate, ErrorMessageContainsMismatchIndex) {
    writeSchema(SCHEMA_PATH, {"a", "b", "c"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    try {
        schema.validate({"a", "wrong", "c"});
        FAIL() << "Expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        // Index 1 should appear in the message
        EXPECT_NE(std::string(e.what()).find("1"), std::string::npos);
    }
}

TEST(FeatureSchemaValidate, OrderMattersDifferentOrderFails) {
    writeSchema(SCHEMA_PATH, {"a", "b"});
    auto schema = FeatureSchema::loadFromJSON(SCHEMA_PATH);
    EXPECT_THROW(schema.validate({"b", "a"}), std::invalid_argument);
}
