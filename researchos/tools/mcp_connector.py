"""MCP 连接器实现。

提供标准的 MCP 服务器连接功能，支持：
- stdio 协议（通过 subprocess 启动 MCP 服务器）
- 自动发现和注册工具
- 错误处理和超时控制
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..runtime.errors import ToolRuntimeError

_LOG = logging.getLogger(__name__)


class StdioMCPClient:
    """通过 stdio 协议连接 MCP 服务器的客户端。

    实现 MCPClientProtocol 和 MCPDiscoveryClientProtocol 接口。
    """

    def __init__(self, name: str, process: asyncio.subprocess.Process):
        self.name = name
        self._process = process
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """初始化 MCP 连接"""
        if self._initialized:
            return

        # 启动后台读取任务
        self._reader_task = asyncio.create_task(self._read_responses())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # 发送 initialize 请求
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "researchos",
                "version": "0.1.0"
            }
        })

        if "error" in response:
            raise ToolRuntimeError(
                "mcp_initialize",
                Exception(f"MCP initialize failed: {response['error']}")
            )

        # 发送 initialized 通知
        await self._send_notification("notifications/initialized")
        self._initialized = True
        _LOG.info(f"MCP client '{self.name}' initialized successfully")

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出所有可用的工具"""
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/list", {})

        if "error" in response:
            raise ToolRuntimeError(
                "mcp_list_tools",
                Exception(f"Failed to list tools: {response['error']}")
            )

        tools = response.get("result", {}).get("tools", [])
        _LOG.info(f"MCP client '{self.name}' discovered {len(tools)} tools")
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用远程工具"""
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        if "error" in response:
            error_data = response["error"]
            raise ToolRuntimeError(
                f"mcp_{self.name}_{tool_name}",
                Exception(f"Tool call failed: {error_data}")
            )

        return response.get("result", {})

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待响应"""
        self._request_id += 1
        request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }

        # 创建 future 等待响应
        future: asyncio.Future = asyncio.Future()
        self._pending_requests[request_id] = future

        # 发送请求
        request_json = json.dumps(request) + "\n"
        self._process.stdin.write(request_json.encode())
        await self._process.stdin.drain()

        # 等待响应（30秒超时）
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
            return response
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise ToolRuntimeError(
                f"mcp_{self.name}",
                Exception(f"Request timeout: {method}")
            )

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发送 JSON-RPC 通知（不需要响应）"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }

        notification_json = json.dumps(notification) + "\n"
        self._process.stdin.write(notification_json.encode())
        await self._process.stdin.drain()

    async def _read_responses(self) -> None:
        """后台任务：持续读取响应"""
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    response = json.loads(line.decode())

                    # 处理响应
                    if "id" in response:
                        request_id = response["id"]
                        future = self._pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(response)

                    # 处理通知（暂时忽略）
                    elif "method" in response:
                        _LOG.debug(f"Received notification: {response['method']}")

                except json.JSONDecodeError as e:
                    _LOG.warning(f"Failed to parse MCP response: {e}, line: {line.decode()}")
                    continue

        except Exception as e:
            _LOG.error(f"MCP reader task failed: {e}")
            # 取消所有待处理的请求
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(e)
            self._pending_requests.clear()

    async def _read_stderr(self) -> None:
        """后台任务：读取 stderr 日志"""
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                _LOG.debug(f"MCP stderr [{self.name}]: {line.decode().strip()}")
        except Exception as e:
            _LOG.error(f"MCP stderr reader failed: {e}")

    async def close(self) -> None:
        """关闭连接"""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, '_stderr_task') and self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass

        if self._process.stdin:
            self._process.stdin.close()

        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

        _LOG.info(f"MCP client '{self.name}' closed")


async def connect_mcp_server(server_config: dict[str, Any]) -> StdioMCPClient:
    """连接到 MCP 服务器。

    Args:
        server_config: 服务器配置，包含：
            - name: 服务器名称
            - command: 启动命令
            - args: 命令参数
            - env: 环境变量（可选）

    Returns:
        StdioMCPClient 实例
    """
    name = server_config.get("name", "unnamed")
    command = server_config.get("command")
    args = server_config.get("args", [])
    env = server_config.get("env", {})

    if not command:
        raise ValueError(f"MCP server '{name}' missing 'command' field")

    _LOG.info(f"Starting MCP server '{name}': {command} {' '.join(args)}")

    # 启动子进程
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**env} if env else None,
        )
    except Exception as e:
        raise ToolRuntimeError(
            f"mcp_connect_{name}",
            Exception(f"Failed to start MCP server: {e}")
        ) from e

    # 创建客户端
    client = StdioMCPClient(name, process)

    # 初始化连接
    try:
        await client.initialize()
    except Exception as e:
        await client.close()
        raise ToolRuntimeError(
            f"mcp_connect_{name}",
            Exception(f"Failed to initialize MCP client: {e}")
        ) from e

    return client
