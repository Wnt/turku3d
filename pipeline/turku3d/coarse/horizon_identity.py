"""Portable render-product identity; source paths remain only in the validation manifest."""
import hashlib
import json


def content_inputs(value, hashes):
    if isinstance(value, dict):
        return {key: content_inputs(item, hashes) for key, item in value.items()}
    if isinstance(value, list):
        if all(isinstance(item, str) and item in hashes for item in value):
            return sorted({hashes[item] for item in value})
        return [content_inputs(item, hashes) for item in value]
    return hashes.get(value, value) if isinstance(value, str) else value


def source_key(recipe, tile, inputs, hashes):
    payload = [recipe, tile, content_inputs(inputs, hashes)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
