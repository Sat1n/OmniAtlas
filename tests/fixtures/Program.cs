using System;
using System.Collections.Generic;
using Rug.Core.Models;

namespace Rug.Core.Services;

public interface IEngine
{
    void Start();
}

public enum EngineState
{
    Idle,
    Running,
}

public struct EngineStats
{
    public int Cycles;
}

public class Engine : IEngine
{
    public string Name { get; set; } = "rug";

    public Engine(string name)
    {
        Name = name;
    }

    public void Start()
    {
        Console.WriteLine(Name);
    }
}
