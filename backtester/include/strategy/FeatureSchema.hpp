#pragma once

#include <fstream>
#include <regex>
#include <stdexcept>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// FeatureSchema — loads feature_schema.json and validates the runtime
// feature-column list against the schema contract.
//
// The parser extracts "name" field values from the JSON array without
// depending on an external JSON library.  The schema file is a project
// artefact that the team controls, so a simple line-by-line extractor is
// sufficient and avoids adding a dependency.
// ---------------------------------------------------------------------------
class FeatureSchema {
public:
    // Feature names in declaration order, as loaded from the schema.
    std::vector<std::string> features;

    // -------------------------------------------------------------------------
    // Factory: parse feature_schema.json and return a FeatureSchema.
    // Throws std::runtime_error if the file cannot be opened or is malformed.
    // -------------------------------------------------------------------------
    static FeatureSchema loadFromJSON(const std::string& path) {
        std::ifstream file(path);
        if (!file.is_open())
            throw std::runtime_error(
                "FeatureSchema: cannot open schema file: " + path);

        FeatureSchema schema;
        std::string line;
        // Match: "name": "some_value"  (any surrounding whitespace)
        const std::regex nameRe(R"([[:space:]]*\"name\"[[:space:]]*:[[:space:]]*\"([^\"]+)\")");
        std::smatch m;

        while (std::getline(file, line)) {
            if (std::regex_search(line, m, nameRe))
                schema.features.push_back(m[1].str());
        }

        if (schema.features.empty())
            throw std::runtime_error(
                "FeatureSchema: no features found in schema file: " + path);

        return schema;
    }

    // -------------------------------------------------------------------------
    // Validate that `actual` matches this schema exactly (count + order).
    // Throws std::invalid_argument on mismatch with a descriptive message.
    // -------------------------------------------------------------------------
    void validate(const std::vector<std::string>& actual) const {
        if (actual.size() != features.size()) {
            throw std::invalid_argument(
                "FeatureSchema: column count mismatch — schema has " +
                std::to_string(features.size()) +
                " features but runtime has " +
                std::to_string(actual.size()));
        }
        for (std::size_t i = 0; i < features.size(); ++i) {
            if (actual[i] != features[i]) {
                throw std::invalid_argument(
                    "FeatureSchema: column mismatch at index " +
                    std::to_string(i) +
                    " — schema expects \"" + features[i] +
                    "\" but runtime has \"" + actual[i] + "\"");
            }
        }
    }
};
