# 神经网络版本的 Jacobian 猜想的研究报告

本报告基于 Moonshine 智能体围绕神经网络版本 Jacobian 猜想的自主数学探索整理而成。Moonshine 是一个面向数学研究的自主科研智能体，能自主探索问题、定位关键数学障碍、凝练可研究方向。

本文呈现Moonshine在复浅层神经网络Jacobian问题上的关键发现：它识别出复sigmoid网络中因周期性与隐藏层维数结构导致的全局单射障碍，并由此发现了复数域与实数域问题之间的本质差异。

## 一、研究背景

经典 Jacobian 猜想是代数几何中的著名公开问题。它讨论复多项式映射

\[
F:\mathbb C^n\to\mathbb C^n
\]

的全局可逆性：若 Jacobian 行列式 \(\det DF\) 非退化，是否能够推出 \(F\) 存在全局多项式逆映射。

这一问题的核心思想是：非退化条件是否足以控制全局结构。在线性代数和微分几何中，Jacobian 行列式非零意味着映射在局部具有反函数；而 Jacobian 猜想关注的是，在多项式结构约束下，常数非零 Jacobian 是否进一步强到足以推出全局可逆。

在神经网络映射中，也可以考虑类似的问题。这里关注的是输入维度与输出维度相同的一隐层前馈神经网络，输出层为线性层，隐藏层使用非线性激活函数。一般形式可写为

\[
F(z)=W^{(2)}\phi(W^{(1)}z+b)+c,
\qquad z\in\mathbb C^n,
\]

其中

\[
W^{(1)}\in\mathbb C^{N\times n},\qquad
W^{(2)}\in\mathbb C^{n\times N},
\]

\(N\) 是隐藏层宽度，\(\phi\) 按分量作用。输出层不再施加激活函数。

与经典多项式映射相比，这类神经网络映射具有不同的函数结构。它们通常不是多项式，而是由线性映射、非线性激活函数和线性输出组合而成。

在这一背景下，研究过程从经典 Jacobian 猜想的思想出发，围绕浅层神经网络映射的 Jacobian 条件、非退化性与全局单射性之间的关系进行了自主探索。

## 二、主要结论

### 2.1 复数域上的神经网络版本 Jacobian 猜想不成立

在复数域上，对于浅层 sigmoid 神经网络，Jacobian 型的非退化条件不能推出全局单射性。也就是说，即使在网络的自然全纯定义域上有

\[
\det DF(z)\neq0,
\]

也不能推出 \(F\) 是全局单射。

这一结论表明，经典 Jacobian 猜想式的直觉在复神经网络映射中会失效。其根本原因不是局部反函数定理失效，而是复激活函数诱导的全局结构会产生不同点具有相同输出的现象。换言之，局部可逆并不能排除复数域上的全局可逆。

因此，复数域上的浅层 sigmoid 网络给出了神经网络版本 Jacobian 猜想的负面结论：

\[
\det DF(z)\neq0\ \text{on the holomorphic domain}
\quad\not\Longrightarrow\quad
F\ \text{is globally injective}.
\]

### 2.2 更有意义的后续研究方向：实数域上的 Jacobian 型问题

在复数域分析中，Moonshine 发现非退化性失效的核心原因来自复 sigmoid 的全局周期结构。由此进一步提出，若要形成更有意义的神经网络版本 Jacobian 问题，应当转向实数域情形。

在实数域上，上述复周期机制不再以同样方式出现。因此，一个自然的后续问题是：设

\[
F:\mathbb R^n\to\mathbb R^n
\]

是一隐层实 sigmoid 神经网络，若

\[
\det DF(x)\neq0\qquad \forall x\in\mathbb R^n,
\]

是否可以推出 \(F\) 全局单射？若不能，还需要增加哪些结构性条件才能推出单射或双射？例如隐藏层宽度、权重矩阵符号结构、输出层核结构、导数一致下界、Hadamard--Plastock 型条件等，都可能成为实数域版本中的关键因素。

因此，这一探索不仅给出了复数域上的反例，也进一步明确了后续研究的方向：复数域中的直接 Jacobian 类比会被周期结构破坏，而实数域上的局部到全局问题更可能形成有实质内容的神经网络版本 Jacobian 理论。

## 三、复数域情形的分情形分析

设研究对象为一隐层复 sigmoid 网络

\[
F(z)=W\sigma(Az+b)+c,
\qquad z\in\mathbb C^n,
\]

其中

\[
A\in\mathbb C^{N\times n},\qquad
W\in\mathbb C^{n\times N},
\]

\(N\) 是隐藏层宽度，\(\sigma\) 为 logistic sigmoid：

\[
\sigma(t)=\frac{1}{1+e^{-t}}.
\]

第 \(i\) 个隐藏神经元的预激活记为

\[
L_i(z)=a_i^Tz+b_i.
\]

由于复 sigmoid 在某些点处有极点，网络的自然全纯定义域为

\[
\Omega=\mathbb C^n\setminus\mathcal P,
\]

其中

\[
\mathcal P=
\bigcup_{i=1}^N\bigcup_{k\in\mathbb Z}
\{z\in\mathbb C^n:L_i(z)=i\pi(2k+1)\}.
\]

以下讨论均在 \(\Omega\) 上进行。

### 3.1 欠参数情形 \(N<n\)

当隐藏层宽度小于输入维度时，矩阵

\[
A:\mathbb C^n\to\mathbb C^N
\]

必然有非零核。于是存在

\[
0\neq v\in\ker A.
\]

对任意 \(z\in\Omega\)，有

\[
A(z+v)+b=Az+b.
\]

因此

\[
\sigma(A(z+v)+b)=\sigma(Az+b),
\]

从而

\[
F(z+v)=F(z).
\]

并且由于预激活值完全相同，若 \(z\in\Omega\)，则 \(z+v\in\Omega\)。因此 \(F\) 不可能在 \(\Omega\) 上单射。因此，在 \(N<n\) 时，网络不可能满足全局单射。

### 3.2 方阵情形 \(N=n\)：周期格机制

当 \(N=n\) 时，隐藏层宽度与输入维度相同。此时导致复数域单射性失败的核心结构是周期格。

复 sigmoid 满足

\[
\sigma(t+2\pi i)=\sigma(t).
\]

因此定义与输入矩阵 \(A\) 相关的周期格

\[
L=\{v\in\mathbb C^n:Av\in(2\pi i\mathbb Z)^n\}.
\]

若 \(v\in L\)，则存在 \(m\in\mathbb Z^n\)，使得

\[
Av=2\pi i m.
\]

于是

\[
A(z+v)+b=Az+b+2\pi i m,
\]

从而

\[
\sigma(A(z+v)+b)=\sigma(Az+b).
\]

因此

\[
F(z+v)=F(z).
\]

这说明周期格中的非零向量会直接产生非单射。

#### 定理：方阵情形的周期格判据

设

\[
F(z)=W\sigma(Az+b)+c,
\]

其中

\[
A,W\in\mathbb C^{n\times n},
\]

且 \(W\) 可逆。令

\[
L=\{v\in\mathbb C^n:Av\in(2\pi i\mathbb Z)^n\}.
\]

则

\[
F\text{ 在 }\Omega\text{ 上单射}
\quad\Longleftrightarrow\quad
L=\{0\}.
\]

**证明：** 若

\[
F(z_1)=F(z_2),
\]

由于 \(W\) 可逆，可得

\[
\sigma(Az_1+b)=\sigma(Az_2+b).
\]

在非极点处，对每个分量有

\[
\sigma(u)=\sigma(v)
\quad\Longleftrightarrow\quad
u-v\in2\pi i\mathbb Z.
\]

因此

\[
A(z_1-z_2)\in(2\pi i\mathbb Z)^n,
\]

即

\[
z_1-z_2\in L.
\]

如果 \(L=\{0\}\)，则 \(z_1=z_2\)，故 \(F\) 单射。

反过来，如果 \(L\neq\{0\}\)，取 \(0\neq v\in L\)，则对任意 \(z\in\Omega\)，有

\[
F(z+v)=F(z),
\]

并且 \(z+v\in\Omega\)。所以 \(F\) 不单射。证毕。

**注：** 在复 sigmoid 的方阵情形中，\(L=\{0\}\) 实际上不会出现。若 \(A\) 奇异，则存在 \(0\neq v\in\ker A\)，从而 \(v\in L\)。若 \(A\) 可逆，则对任意非零整数向量 \(m\in\mathbb Z^n\)，

\[
v=A^{-1}(2\pi i m)
\]

是非零向量，且满足 \(Av=2\pi i m\)，因此 \(v\in L\)。所以 \(L\neq\{0\}\)。

因此，在 \(N=n\) 的复 sigmoid 方阵情形下，总可以得到由周期格产生的非单射反例。特别地，当 \(A,W\) 均可逆时，Jacobian 为

\[
DF(z)=W\operatorname{diag}(\sigma'(Az+b))A.
\]

在 \(\Omega\) 上，\(\sigma'\) 没有零点，所以

\[
\det DF(z)\neq0.
\]

但 \(F\) 仍然不是单射。这正是复数域上“局部 Jacobian 非退化不能推出全局单射”的核心反例机制。

一个最简单的例子是

\[
F(z_1,
\ldots,z_n)=(\sigma(z_1),\ldots,\sigma(z_n)).
\]

其 Jacobian 行列式在自然定义域上处处非零，但由于

\[
F(z_1+2\pi i,z_2,
\ldots,z_n)=F(z_1,z_2,
\ldots,z_n),
\]

故不是单射。

### 3.3 过参数情形 \(N>n\)：输出层核抵消机制

当 \(N>n\) 时，输出矩阵

\[
W:\mathbb C^N\to\mathbb C^n
\]

一定存在非零核。此时，即使隐藏层输出发生变化，只要变化量落入 \(\ker W\)，最终输出仍可能不变。

也就是说，若

\[
F(z_1)=F(z_2),
\]

只能推出

\[
\sigma(Az_1+b)-\sigma(Az_2+b)\in\ker W,
\]

而不能推出隐藏层输出逐分量相等。这个机制不同于方阵情形中的周期格平移，可称为输出层核抵消。

下面给出一个典型的二维三隐元例子，说明即使周期格为零，过参数网络仍可能不是单射。

考虑

\[
F(z_1,z_2)=
\begin{pmatrix}
\sigma(z_1+\sqrt2 z_2)-\sigma(z_1+z_2)\\
\sigma(\sqrt3 z_1+z_2)-\sigma(z_1+z_2)
\end{pmatrix}.
\]

对应矩阵为

\[
A=
\begin{pmatrix}
1&\sqrt2\\
\sqrt3&1\\
1&1
\end{pmatrix},
\qquad
W=
\begin{pmatrix}
1&0&-1\\
0&1&-1
\end{pmatrix}.
\]

其中 \(n=2\)，\(N=3\)，所以这是一个过参数情形。

取

\[
z=\left(
\frac{2\pi i}{\sqrt3-1},
\frac{2\pi i}{\sqrt2-1}
\right).
\]

则

\[
(z_1+\sqrt2z_2)-(z_1+z_2)=(\sqrt2-1)z_2=2\pi i,
\]

并且

\[
(\sqrt3z_1+z_2)-(z_1+z_2)=(\sqrt3-1)z_1=2\pi i.
\]

由 sigmoid 的周期性

\[
\sigma(t+2\pi i)=\sigma(t),
\]

可得

\[
\sigma(z_1+\sqrt2 z_2)=\sigma(z_1+z_2),
\]

以及

\[
\sigma(\sqrt3 z_1+z_2)=\sigma(z_1+z_2).
\]

因此

\[
F(z)=
\begin{pmatrix}
0\\
0
\end{pmatrix}.
\]

另一方面，

\[
F(0)=\begin{pmatrix}
\sigma(0)-\sigma(0)\\
\sigma(0)-\sigma(0)
\end{pmatrix}=
\begin{pmatrix}
0\\
0
\end{pmatrix}.
\]

所以

\[
F(z)=F(0),\qquad z\neq0.
\]

这说明 \(F\) 不是单射。

同时，该例的周期格为

\[
L(A)=\{v\in\mathbb C^2:Av\in(2\pi i\mathbb Z)^3\}.
\]

若 \(v\in L(A)\)，则周期条件会导出一个关于 \(1,\sqrt2,\sqrt3,\sqrt6\) 的有理线性关系。由于\(1,\sqrt2,\sqrt3,\sqrt6\)在 \(\mathbb Q\) 上线性无关，该关系只能是平凡关系，因此 \(v=0\)。故

\[
L(A)=\{0\}.
\]

因此，该反例表明：在 \(N>n\) 的过参数情形中，非单射不一定来自非零周期格。即使

\[
L(A)=\{0\},
\]

输出层核抵消仍然可以使不同输入具有相同输出。由此可见，周期格判据在方阵情形中是完整的，但在过参数情形中并不足以刻画全局单射性。

## 四、总结

复数域上的浅层 sigmoid 神经网络与经典多项式映射有本质差异。对于经典 Jacobian 猜想，常数非零 Jacobian 是一个强代数条件；而在复 sigmoid 网络中，局部 Jacobian 非退化无法控制由周期结构和输出层核结构造成的全局重复。

具体而言：

1. 当 \(N<n\) 时，输入到隐藏层的线性映射有非零核，网络必不单射；
2. 当 \(N=n\) 时，周期格给出方阵情形的单射判据，但该情形下周期格实际上总是非零，因此存在非退化但非单射的反例；
3. 当 \(N>n\) 时，输出层核抵消成为新的非单射机制，即使周期格为零也不能保证单射。

因此，复数域上的神经网络版本 Jacobian 猜想不成立。更有意义的后续方向是在实数域上研究神经网络映射的 Jacobian 条件与全局单射性之间的关系。
