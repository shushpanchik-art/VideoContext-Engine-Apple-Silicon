import json
import tempfile
import unittest
from pathlib import Path

from scripts.vce_observed_run import (
    encode_form_value,
    flatten_timing_metrics,
    get_form_schema,
    multipart_body,
    resolve_form_fields,
    validate_prompt_limits,
)


class ObservedRunHelpersTests(unittest.TestCase):
    def test_form_value_encoding(self):
        self.assertEqual(encode_form_value(True), "true")
        self.assertEqual(encode_form_value(False), "false")
        self.assertEqual(encode_form_value(12), "12")
        self.assertEqual(encode_form_value(1.25), "1.25")
        self.assertEqual(encode_form_value({"a": 1}), '{"a":1}')
        self.assertEqual(encode_form_value(["ru", "en"]), '["ru","en"]')

    def test_flatten_only_numeric_timing_fields(self):
        data = {
            "total_process_time": 10.954,
            "scene_count": 3,
            "whisper": {"inference_time": 2.599, "model": "large"},
            "parallelism": {"whisper_vlm_overlap_seconds": 0.0},
        }
        flattened = dict(flatten_timing_metrics(data))
        self.assertEqual(flattened["total_process_time"], 10.954)
        self.assertEqual(flattened["whisper.inference_time"], 2.599)
        self.assertEqual(flattened["parallelism.whisper_vlm_overlap_seconds"], 0.0)
        self.assertNotIn("scene_count", flattened)

    def test_openapi_form_schema_and_request_overrides(self):
        spec = {
            "paths": {
                "/api/v1/analyze": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {"$ref": "#/components/schemas/Body"}
                                }
                            }
                        }
                    }
                },
                "/api/v1/progress/{job_id}": {"get": {}},
            },
            "components": {
                "schemas": {
                    "Body": {
                        "properties": {
                            "video_file": {"type": "string"},
                            "skip_audio": {"type": "boolean", "default": True},
                            "skip_visual": {"type": "boolean", "default": True},
                            "analysis_mode": {"type": "string", "default": "ocr_only"},
                            "generate_summary": {"type": "boolean", "default": False},
                            "generate_txt": {"type": "boolean", "default": True},
                            "generate_json": {"type": "boolean", "default": True},
                            "response_format": {"type": "string", "default": "text"},
                            "enable_stage_timing": {"type": "boolean", "default": False},
                            "enable_mlx_whisper": {"type": "boolean", "default": False},
                            "text_llm_enabled": {"type": "boolean", "default": False},
                            "job_id": {"type": "string", "default": ""},
                        }
                    }
                }
            },
        }
        props, progress_path = get_form_schema(spec)
        self.assertIn("video_file", props)
        self.assertEqual(progress_path, "/api/v1/progress/{job_id}")
        fields, origins = resolve_form_fields({}, props)
        self.assertEqual(fields["skip_audio"], False)
        self.assertEqual(fields["skip_visual"], False)
        self.assertEqual(fields["analysis_mode"], "full")
        self.assertEqual(fields["generate_summary"], True)
        self.assertEqual(fields["generate_txt"], False)
        self.assertEqual(fields["generate_json"], False)
        self.assertEqual(fields["response_format"], "json")
        self.assertEqual(fields["enable_stage_timing"], True)
        self.assertEqual(fields["enable_mlx_whisper"], True)
        self.assertEqual(fields["text_llm_enabled"], True)
        self.assertIn("job_id", origins)

    def test_prompt_limits_are_checked_before_upload(self):
        fields = {"visual_user_prompt": "12345", "summary_user_prompt": "ok"}
        props = {
            "visual_user_prompt": {"maxLength": 4},
            "summary_user_prompt": {"maxLength": 10},
        }
        problems = validate_prompt_limits(fields, props)
        self.assertEqual(len(problems), 1)
        self.assertIn("visual_user_prompt", problems[0])

    def test_multipart_contains_fields_and_binary_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "sample.mp4"
            video.write_bytes(b"\x00MP4\xff")
            body = multipart_body({"skip_audio": False, "job_id": "obs_test"}, video, "boundary42", "obs_test")
            self.assertIn(b'name="skip_audio"\r\n\r\nfalse', body)
            self.assertIn(b'name="job_id"\r\n\r\nobs_test', body)
            self.assertIn(b"\x00MP4\xff", body)
            self.assertTrue(body.endswith(b"\r\n--boundary42--\r\n"))


if __name__ == "__main__":
    unittest.main()
