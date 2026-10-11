.PHONY: test lint examples clean

test:
	python3 tests/test_skills.py

lint:
	python3 -m py_compile skills/*/scripts/*.py tests/*.py
	@command -v terraform >/dev/null && terraform fmt -check -recursive skills/apigee-x-provisioning/terraform || echo "terraform not installed: skipped fmt check"
	bash -n skills/apigee-proxy-builder/scripts/deploy_bundle.sh install.sh

# Generate one example bundle of each kind into ./build (offline, no Apigee access needed)
examples:
	rm -rf build && mkdir -p build
	python3 skills/apigee-api-proxy/scripts/scaffold_api_proxy.py --out build/orders-v1 --name orders-v1 \
	  --basepath /orders/v1 --target https://mocktarget.apigee.net --auth apikey --quota --cors "*" \
	  --oas examples/specs/orders-v1.json --cache-ttl 60 --health
	python3 skills/apigee-api-proxy/scripts/scaffold_shared_flow.py --out build/sf-security --name sf-security \
	  --features apikey,spikearrest,threat,strip-auth
	python3 skills/apigee-ai-gateway/scripts/scaffold_ai_gateway.py --out build/ai-gateway-v1 --project my-project \
	  --token-quota --prompt-token-limit 5000pm --claude --auto-route
	python3 skills/apigee-mcp-gateway/scripts/scaffold_mcp_proxy.py --out build/orders-mcp --name orders-mcp \
	  --basepath /orders/mcp --target https://orders-mcp-xyz.a.run.app/mcp --tools searchOrders,getOrder,issueRefund \
	  --persona "Support:searchOrders,getOrder,issueRefund" --persona "Insights:searchOrders" \
	  --limit 'issueRefund:amount:50:Support:REFUND_LIMIT:Refunds over $$50 need supervisor approval.'
	for d in build/*; do python3 skills/apigee-proxy-builder/scripts/validate_bundle.py $$d --strict || exit 1; done

clean:
	rm -rf build
